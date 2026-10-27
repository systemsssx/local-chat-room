"""房间数据 / SSE 订阅 / 在线成员。

三块，按"寿命"分开：
    room_store —— 房间消息：内存做热数据 + SQLite 做持久化
    hub        —— SSE 订阅者：每个房间一组队列、每个用户一组队列（重启即断）
    （在线成员不再单独记表：有活跃 SSE 连接就是在线，这样最准）

为什么用队列而不是直接 send：
    一条消息要推给很多人。广播方只把字符串塞进各自的队列就返回，
    真正的写出由每个连接自己的协程负责 —— 某个客户端网速慢不会拖住别人。

**权限隔离的唯一入口是 require_member()**：任何读房间数据的地方都先过它，
别在路由里各写一遍（漏一个就是越权读别人的群）。
"""

import asyncio
import json
import secrets
import sqlite3
import uuid
from pathlib import Path

from fastapi import HTTPException, status

from . import config, db

# ══════════════ 0. 权限（唯一入口） ══════════════

def require_room(room_id: int) -> dict:
    room = db.room_by_id(room_id)
    if room is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "房间不存在")
    return room


def require_member(room_id: int, user_id: int) -> dict:
    """不是这个房间的成员，一律 403 —— 数据隔离靠的就是每个入口都调它"""
    room = require_room(room_id)
    if not db.is_member(room_id, user_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "你不在这个房间里")
    return room


# ══════════════ 1. 房间码 ══════════════

def new_code() -> str:
    return "".join(secrets.choice(config.ROOM_CODE_ALPHABET) for _ in range(config.ROOM_CODE_LEN))


def unique_code() -> str:
    """随机 + 唯一索引兜底：撞了就重试（不是自增 id —— 顺序号能被一路试过去）"""
    for _ in range(config.ROOM_CODE_RETRY):
        code = new_code()
        if db.room_by_code(code) is None:
            return code
    raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "房间码生成失败，请重试")


# ══════════════ 2. 消息存储 ══════════════

def _clean_file(raw, room_id: int) -> dict:
    """把客户端传来的 `file` 对象**白名单重建**一遍。

    为什么不能直接信：`file` 是客户端拼的 JSON —— 可以塞 `url: "javascript:…"`、
    塞一个 3MB 的 preview、塞任意文件名，而前端会把它直接放进 `<a href>` / `<img src>`。
    所以只认我们自己的上传记录：

      · url 必须是 `/uploads/<文件名>`（没有多余路径段，防穿越）；
      · 那个文件**确实传到了这个房间**（查 uploads 表），否则等于是引用别人的文件；
      · 其余字段按类型截断，多余键一律丢掉。
    """
    if not isinstance(raw, dict):
        raise ValueError("file 字段格式不对")

    url = str(raw.get("url") or "")
    name = Path(url).name
    if not name or name != url.removeprefix("/uploads/") or not url.startswith("/uploads/"):
        raise ValueError("file.url 必须是本服务上传接口返回的地址")
    if db.upload_room(name) != room_id:
        raise ValueError("这个文件不是上传到当前房间的")

    kind = raw.get("kind") if raw.get("kind") in ("image", "file") else "file"
    size = raw.get("size")
    out = {
        "id": str(raw.get("id") or name.split(".")[0])[:32],
        "url": f"/uploads/{name}",
        "name": str(raw.get("name") or name)[:120],
        "size": int(size) if isinstance(size, (int, float)) and size >= 0 else 0,
        "mime": str(raw.get("mime") or "")[:100],
        "kind": kind,
    }
    if kind == "file":
        preview = str(raw.get("preview") or "")[:2000]
        if preview:
            out["preview"] = preview
        if isinstance(raw.get("lines"), int):
            out["lines"] = raw["lines"]
        if raw.get("encoding"):
            out["encoding"] = str(raw["encoding"])[:20]
    return out


class RoomStore:
    """消息的唯一入口：写库 + 内存热数据。读走内存，向上翻页查库。"""

    def __init__(self) -> None:
        self._rooms: dict[int, list[dict]] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    async def _ensure(self, room_id: int) -> list[dict]:
        """房间第一次被访问时，把最近的消息从库里载入内存。

        ⚠️ 必须加锁：两个请求同时进一个冷房间时，各自 await 一次载入，
        后写入的会把前一个桶**覆盖**掉 —— 先拿到旧桶的协程再 append，
        那条消息就落进了"孤儿桶"，从此 `since()` 永远补不到它（不报错，但消息丢了）。
        """
        bucket = self._rooms.get(room_id)
        if bucket is not None:
            return bucket
        lock = self._locks.setdefault(room_id, asyncio.Lock())
        async with lock:
            bucket = self._rooms.get(room_id)          # 拿到锁后再看一眼（双重检查）
            if bucket is None:
                bucket = await asyncio.to_thread(
                    db.recent_messages, room_id, config.MEMORY_MESSAGES
                )
                self._rooms[room_id] = bucket
        return bucket

    def drop(self, room_id: int) -> None:
        self._rooms.pop(room_id, None)
        self._locks.pop(room_id, None)

    async def append(self, room_id: int, user: dict, payload: dict) -> dict:
        """追加一条消息。

        大小 / 类型 / file 结构的校验**收口在这里**，不放在路由里 ——
        不然将来多一条上行路径（比如再挂个别的接口）就会绕过限制。
        失败一律抛 ValueError，由调用方翻译成 HTTP 码。
        """
        text = payload.get("text") or ""
        if len(text.encode("utf-8")) > config.MAX_TEXT_BYTES:
            raise ValueError(f"单条内容超过 {config.MAX_TEXT_BYTES // 1024}KB")

        kind = payload.get("type") or "text"
        if kind not in ("text", "image", "file"):
            raise ValueError(f"不认识的消息类型：{kind}")
        if kind in ("image", "file") and not payload.get("file"):
            raise ValueError(f"{kind} 类型的消息必须带 file 字段")
        if kind == "text" and not text.strip():
            raise ValueError("消息不能是空的")

        # file 是客户端拼的 JSON，必须白名单重建一遍再入库
        file_obj = _clean_file(payload.get("file"), room_id) if kind != "text" else None

        msg_id = str(uuid.uuid4())
        seq = await asyncio.to_thread(
            db.append_message, room_id, user["id"], kind, text, file_obj, msg_id
        )
        msg = {
            "seq": seq,
            "id": msg_id,
            "roomId": room_id,
            "userId": user["id"],
            "username": user["username"],
            "type": kind,
            "text": text,
            "file": file_obj,
            "at": db.now_ms(),
        }
        await self._ensure(room_id)
        self._rooms[room_id].append(msg)      # 只往"当前登记在册"的那个桶里追加
        return msg

    async def since(self, room_id: int, since_seq: int) -> list[dict]:
        """seq 之后的消息（重连补齐 / 切换房间补齐都用它）"""
        bucket = await self._ensure(room_id)
        # 内存里只留最近 500 条：要的差额比内存还早，就直接查库，保证不丢
        if bucket and since_seq < bucket[0]["seq"] - 1:
            return await asyncio.to_thread(db.messages_since, room_id, since_seq)
        return [m for m in bucket if m["seq"] > since_seq]

    async def latest_page(self, room_id: int, limit: int = config.HISTORY_LIMIT) -> list[dict]:
        return await asyncio.to_thread(db.recent_messages, room_id, limit)

    async def page_before(self, room_id: int, before_seq: int,
                          limit: int = config.PAGE_LIMIT) -> list[dict]:
        return await asyncio.to_thread(db.messages_before, room_id, before_seq, limit)


room_store = RoomStore()


# ══════════════ 3. SSE 订阅 ══════════════

def frame(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class Hub:
    """SSE 订阅表。

    一条连接登记两次：
      · 房间频道 —— 推这个房间的消息、成员变化
      · 用户频道 —— 推"我的房间列表"变化（被拉进群、被踢、有人发消息导致的未读）
    """

    def __init__(self) -> None:
        self._room: dict[int, dict[asyncio.Queue, int]] = {}   # room_id -> {queue: user_id}
        self._user: dict[int, set[asyncio.Queue]] = {}         # user_id -> queues

    # —— 登记 / 注销（room_id=None 表示只订"我的房间列表"频道）——
    def subscribe(self, room_id: int | None, user_id: int, q: asyncio.Queue) -> None:
        if room_id is not None:
            self._room.setdefault(room_id, {})[q] = user_id
        self._user.setdefault(user_id, set()).add(q)

    def unsubscribe(self, room_id: int | None, user_id: int, q: asyncio.Queue) -> None:
        if room_id is not None:
            conns = self._room.get(room_id)
            if conns is not None:
                conns.pop(q, None)
                if not conns:
                    self._room.pop(room_id, None)
        queues = self._user.get(user_id)
        if queues is not None:
            queues.discard(q)
            if not queues:
                self._user.pop(user_id, None)

    # —— 在线（有活跃 SSE 连接就是在线的）——
    def online_ids(self, room_id: int) -> set[int]:
        return set(self._room.get(room_id, {}).values())

    def online_in_room(self, room_id: int) -> int:
        return len(set(self._room.get(room_id, {}).values()))

    # —— 推送 ——
    def push_room(self, room_id: int, event: str, data: dict) -> None:
        text = frame(event, data)
        for q in list(self._room.get(room_id, {})):
            _safe_put(q, text)

    def push_user(self, user_id: int, event: str, data: dict) -> None:
        text = frame(event, data)
        for q in list(self._user.get(user_id, ())):
            _safe_put(q, text)

    def push_members_except(self, room_id: int, event: str, data: dict,
                            skip_viewers_of: int | None = None) -> None:
        """推给这个房间所有成员的**用户频道**（不看这个房间的人也会收到，用来更新左栏未读）。

        `skip_viewers_of`：正在看这个房间的连接跳过 —— 他们已经从房间频道拿到消息了。
        """
        text = frame(event, data)
        viewers = set(self._room.get(skip_viewers_of, {})) if skip_viewers_of else set()
        for user_id in db.member_ids(room_id):
            for q in list(self._user.get(user_id, ())):
                if q in viewers:
                    continue
                _safe_put(q, text)


def _safe_put(q: asyncio.Queue, text: str) -> None:
    try:
        q.put_nowait(text)
    except asyncio.QueueFull:      # 客户端太慢：丢这一条，绝不阻塞广播方
        pass


hub = Hub()


# ══════════════ 4. 房间服务（路由直接调这些） ══════════════

def _validate_name(name: str) -> str:
    value = (name or "").strip()
    if not value:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "房间名不能为空")
    if len(value) > config.ROOM_NAME_MAX:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"房间名最长 {config.ROOM_NAME_MAX} 个字")
    return value


def members_view(room_id: int) -> list[dict]:
    """成员列表 + 在线标记（在线 = 有活跃 SSE 连接）"""
    online = hub.online_ids(room_id)
    members = db.members_of(room_id)
    for m in members:
        m["online"] = m["userId"] in online
    members.sort(key=lambda m: (0 if m["online"] else 1,
                                0 if m["role"] == "owner" else 1,
                                m["joinedAt"]))
    return members


def rooms_payload(user_id: int) -> list[dict]:
    rooms = db.rooms_of_user(user_id)
    for room in rooms:
        room["online"] = hub.online_in_room(room["id"])
    return rooms


def create_room(user: dict, name: str, visibility: str) -> dict:
    name = _validate_name(name)
    visibility = visibility if visibility in ("private", "public") else "private"
    for _ in range(config.ROOM_CODE_RETRY):
        try:
            room = db.create_room(new_code(), name, user["id"], visibility)
            break
        except sqlite3.IntegrityError:             # 房间码撞了（唯一索引兜底）→ 换一个再来
            continue
    else:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "房间码生成失败，请重试")

    hub.push_user(user["id"], "rooms", {"rooms": rooms_payload(user["id"])})
    return room


def join_room(user: dict, code: str) -> tuple[dict, bool]:
    room = db.room_by_code((code or "").strip().upper())
    if room is None:
        # 不区分"码不存在"和"你不该进" —— 少给攻击者一点信息
        raise HTTPException(status.HTTP_404_NOT_FOUND, "房间码无效")
    if db.is_member(room["id"], user["id"]):
        return room, False
    if len(db.member_ids(room["id"])) >= config.ROOM_MEMBERS_MAX:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"房间人数已满（上限 {config.ROOM_MEMBERS_MAX}）")
    db.add_member(room["id"], user["id"])
    hub.push_user(user["id"], "rooms", {"rooms": rooms_payload(user["id"])})
    hub.push_room(room["id"], "members", {"members": members_view(room["id"])})
    hub.push_members_except(room["id"], "room-updated",
                            {"roomId": room["id"], "members": len(db.member_ids(room["id"]))})
    return db.room_by_id(room["id"]), True


def leave_room(user: dict, room_id: int) -> None:
    room = require_member(room_id, user["id"])
    is_owner = room["ownerId"] == user["id"]
    db.remove_member(room_id, user["id"])

    remaining = db.members_of(room_id)
    if not remaining:
        db.delete_room(room_id)              # 最后一个人走了，房间和消息一起清掉
        room_store.drop(room_id)
        hub.push_user(user["id"], "rooms", {"rooms": rooms_payload(user["id"])})
        return

    if is_owner:                             # 房主退群：交给最早加入的成员，别让房间没人管
        db.transfer_owner(room_id, remaining[0]["userId"])
    hub.push_user(user["id"], "rooms", {"rooms": rooms_payload(user["id"])})
    hub.push_room(room_id, "members", {"members": members_view(room_id)})
    hub.push_members_except(room_id, "room-updated",
                            {"roomId": room_id, "members": len(remaining)})


def dissolve_room(user: dict, room_id: int) -> None:
    """解散房间（仅房主）：通知所有成员 → 删房间（外键级联把成员和消息一起带走）"""
    room = require_member(room_id, user["id"])
    if room["ownerId"] != user["id"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "只有房主能解散房间")
    for uid in db.member_ids(room_id):
        hub.push_user(uid, "room-gone", {"roomId": room_id})
        hub.push_user(uid, "rooms", {"rooms": rooms_payload(uid)})
    db.delete_room(room_id)
    room_store.drop(room_id)


def reset_code(user: dict, room_id: int) -> str:
    room = require_member(room_id, user["id"])
    if room["ownerId"] != user["id"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "只有房主能重置房间码")
    code = unique_code()
    db.set_room_code(room_id, code)
    rooms = rooms_payload(user["id"])
    hub.push_user(user["id"], "rooms", {"rooms": rooms})
    hub.push_members_except(room_id, "room-updated", {"roomId": room_id, "code": code})
    return code


async def room_state(user: dict, room_id: int, since: int = 0) -> dict:
    """首屏 / 重连 / 切房间都走它：房间 + 消息差额 + 成员，并把未读清零"""
    room = require_member(room_id, user["id"])
    latest = db.latest_seq(room_id)

    if since > 0:
        messages = await room_store.since(room_id, since)
    else:
        messages = await room_store.latest_page(room_id, config.HISTORY_LIMIT)

    await asyncio.to_thread(db.touch_last_read, room_id, user["id"], latest)

    room = dict(room)
    room["members"] = len(db.member_ids(room_id))
    return {
        "room": room,
        "messages": messages,
        "members": members_view(room_id),
        "latestSeq": latest,
        "hasMore": bool(messages) and messages[0]["seq"] > 1,
    }


async def post_message(user: dict, room_id: int, payload: dict) -> dict:
    require_member(room_id, user["id"])
    try:
        msg = await room_store.append(room_id, user, payload)
    except ValueError as err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(err)) from err

    hub.push_room(room_id, "messages", {"messages": [msg]})
    # 自己发的消息不该算成"未读"：顺手把已读位置推到自己这条
    await asyncio.to_thread(db.touch_last_read, room_id, user["id"], msg["seq"])
    hub.push_members_except(
        room_id, "room-updated",
        {"roomId": room_id, "lastSeq": msg["seq"], "lastAt": msg["at"],
         "lastText": _preview(msg), "lastUser": msg["username"]},
        skip_viewers_of=room_id,
    )
    return msg


def _preview(msg: dict) -> str:
    if msg["type"] == "image":
        body = "[图片]"
    elif msg["type"] == "file":
        body = "[文件]"
    else:
        body = (msg["text"] or "").replace("\n", " ")[:60]
    return f"{msg['username']}：{body}"
