"""房间数据 / 连接订阅 / 在线成员。

拆成三块，因为它们的"寿命"不一样：
    room_store —— 房间与消息：**内存做热数据 + SQLite 做持久化（写穿）**
    hub        —— 连接订阅者：SSE 和 WebSocket 各一组 asyncio 队列，重启即断
    presence   —— 谁在线：靠"最近有没有动静"判断（轮询客户端没有长连接，只能这样）

为什么要用队列而不是直接 send？
    一次「发消息」要推给很多人（同房间所有 SSE 订阅者 + 所有 WS 连接）。
    广播方只把数据塞进各自的队列就返回，**真正的写出由每个连接自己的协程负责** ——
    这样某个客户端网速慢也不会拖住别人。
"""

import asyncio
import json
import time
import uuid

from . import config, db


def _now_ms() -> int:
    return int(time.time() * 1000)


# ══════════════ 1. 房间与消息 ══════════════

class RoomStore:
    """消息的唯一入口：写内存 + 写库，读走内存，历史查库。"""

    def __init__(self) -> None:
        self._rooms: dict[str, list[dict]] = {}
        self._seq = 0

    def init(self) -> int:
        """服务启动时：把序号接上历史最大值（**不能让 seq 重置**，前端靠它去重和补差额）"""
        self._seq = db.max_seq()
        return self._seq

    @property
    def seq(self) -> int:
        return self._seq

    @property
    def room_count(self) -> int:
        return max(len(self._rooms), len(db.all_rooms()))

    async def _ensure(self, room: str) -> list[dict]:
        """房间第一次被访问时，把最近的消息从库里载入内存"""
        if room not in self._rooms:
            self._rooms[room] = await asyncio.to_thread(
                db.last_messages, room, config.MEMORY_MESSAGES
            )
        return self._rooms[room]

    async def push(self, room: str, payload: dict, client: dict) -> dict:
        """追加一条消息。

        ⚠️ 顺序要紧：**先 ensure 载入内存 → 再落库 → 最后 append**。
        反过来的话，冷房间第一次写消息会先落库、后被 _ensure() 从库里载入一次、再 append 一次 → 重复。

        ⚠️ 校验收口在这里，而不是只放在 HTTP 路由里：
        HTTP 和 WebSocket **两条上行路径都走这个函数**，只在路由里校验的话，
        WS 那条路会整个绕过大小限制（实测确实绕过了）。校验失败一律抛 ValueError，
        由各自的调用方翻译成 413 / error 帧，两条路的行为才一致。
        """
        text = payload.get("text") or ""
        if len(text.encode("utf-8")) > config.MAX_TEXT_BYTES:
            raise ValueError(f"单条内容超过 {config.MAX_TEXT_BYTES // 1024}KB")

        kind = payload.get("type") or "text"
        if kind not in ("text", "image", "file"):
            raise ValueError(f"不认识的消息类型：{kind}")
        if kind in ("image", "file") and not payload.get("file"):
            raise ValueError(f"{kind} 类型的消息必须带 file 字段")

        self._seq += 1
        msg = {
            "seq": self._seq,
            "id": str(uuid.uuid4()),
            "room": room,
            "clientId": client.get("clientId"),
            "nickname": client.get("nickname"),
            "ip": client.get("ip"),
            "type": kind,
            "text": text,
            "file": payload.get("file"),
            "source": payload.get("source") or "text",     # 哪种输入模式产生的（只做标记）
            "transport": client.get("transport"),          # 哪条管道送出的（只做标记）
            "at": _now_ms(),
        }

        bucket = await self._ensure(room)
        await asyncio.to_thread(db.save_message, msg)      # 落库失败就不该出现在窗口里
        bucket.append(msg)
        return msg

    async def since(self, room: str, since_seq: int) -> list[dict]:
        """某房间在 seq 之后的消息（切换传输补差额 / 轮询下行 / 重连补齐都用它）"""
        bucket = await self._ensure(room)
        return [m for m in bucket if m["seq"] > since_seq]

    async def history(self, room: str, limit: int = config.HISTORY_LIMIT) -> list[dict]:
        """最近 N 条（倒序）—— 直接查库，所以服务重启后照样有"""
        return await asyncio.to_thread(db.recent_messages, room, limit)


room_store = RoomStore()


# ══════════════ 2. 连接订阅（SSE / WebSocket） ══════════════

class Hub:
    """每个房间两组队列：一组给 SSE 连接，一组给 WebSocket 连接。

    队列里放的是**已经序列化好的字符串**：
      · SSE 队列 → 直接写进 HTTP 响应流
      · WS  队列 → 直接 websocket.send_text()
    """

    def __init__(self) -> None:
        self._sse: dict[str, set[asyncio.Queue]] = {}
        self._ws: dict[str, set[asyncio.Queue]] = {}

    # —— 登记 / 注销 ——
    def add_sse(self, room: str, q: asyncio.Queue) -> None:
        self._sse.setdefault(room, set()).add(q)

    def remove_sse(self, room: str, q: asyncio.Queue) -> None:
        self._sse.get(room, set()).discard(q)

    def add_ws(self, room: str, q: asyncio.Queue) -> None:
        self._ws.setdefault(room, set()).add(q)

    def remove_ws(self, room: str, q: asyncio.Queue) -> None:
        self._ws.get(room, set()).discard(q)

    def sse_count(self, room: str) -> int:
        return len(self._sse.get(room, ()))

    def ws_count(self, room: str) -> int:
        return len(self._ws.get(room, ()))

    # —— 广播 ——
    def broadcast_messages(self, room: str, messages: list[dict]) -> None:
        """新消息：推给该房间所有 SSE 订阅者和 WebSocket 连接"""
        sse_frame = "event: messages\ndata: " + json.dumps(
            {"messages": messages}, ensure_ascii=False
        ) + "\n\n"
        for q in list(self._sse.get(room, ())):
            q.put_nowait(sse_frame)

        ws_frame = json.dumps({"type": "broadcast", "messages": messages}, ensure_ascii=False)
        for q in list(self._ws.get(room, ())):
            q.put_nowait(ws_frame)

    def broadcast_members(self, room: str, members: list[dict]) -> None:
        """在线成员变了，通知该房间所有人"""
        sse_frame = "event: members\ndata: " + json.dumps(
            {"members": members}, ensure_ascii=False
        ) + "\n\n"
        for q in list(self._sse.get(room, ())):
            q.put_nowait(sse_frame)

        ws_frame = json.dumps({"type": "members", "members": members}, ensure_ascii=False)
        for q in list(self._ws.get(room, ())):
            q.put_nowait(ws_frame)


hub = Hub()


# ══════════════ 3. 在线成员 ══════════════

class Presence:
    """谁在线。轮询客户端没有长连接，只能靠"最近 N 秒有没有动静"来判断。"""

    def __init__(self) -> None:
        self._clients: dict[str, dict] = {}

    def touch(self, ip: str, room: str, info: dict) -> dict:
        cid = str(info.get("clientId") or "anon")
        prev = self._clients.get(cid, {"connected": False})
        client = {
            **prev,
            "clientId": cid,
            "room": room,
            "nickname": info.get("nickname") or prev.get("nickname") or "匿名",
            "transport": info.get("transport") or prev.get("transport") or "polling",
            "ip": prev.get("ip") or ip,
            "at": _now_ms(),
        }
        self._clients[cid] = client
        return client

    def set_connected(self, client_id: str, connected: bool) -> None:
        c = self._clients.get(client_id)
        if c is not None:
            c["connected"] = connected
            if not connected:
                c["at"] = _now_ms()

    def list(self, room: str) -> list[dict]:
        now = _now_ms()
        ttl_ms = config.MEMBER_TTL_SEC * 1000
        out = []
        for c in self._clients.values():
            if c.get("room") != room:
                continue
            fresh = now - c["at"] < ttl_ms
            if not fresh and not c.get("connected"):
                continue
            out.append({
                "clientId": c["clientId"],
                "nickname": c["nickname"],
                "ip": c["ip"],
                "transport": c["transport"],
                "at": c["at"],
                "stale": not c.get("connected"),   # 没有长连接的（轮询客户端）标黄
            })
        return sorted(out, key=lambda x: x["at"])


presence = Presence()
