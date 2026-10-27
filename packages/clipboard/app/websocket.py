"""任务三 · WebSocket 双向同步。

与 SSE 的本质区别：握手（一次带 Upgrade 的 HTTP 请求）之后，这条 TCP 连接
**不再按 HTTP 说话**，改用 WebSocket 自己的帧协议 —— 两端地位对等，谁都能随时发帧。

消息类型（JSON）：
    客户端 → 服务端：hello（加入/重连补齐）· send（发消息）· ping
    服务端 → 客户端：welcome · broadcast · members · ack · error · pong

FastAPI 里用 @router.websocket 声明；每个连接配一个"发件箱"队列，
广播时只往队列里塞数据，真正的写出由这个连接自己的协程负责（慢客户端不会拖住别人）。
"""

import asyncio
import json
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from . import config
from .rooms import hub, presence, room_store

router = APIRouter()


def _valid_room(room: str) -> bool:
    import re
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,32}", room or ""))


def client_ip(websocket: WebSocket) -> str:
    fwd = websocket.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return websocket.client.host if websocket.client else "127.0.0.1"


async def _drain(websocket: WebSocket, outbox: asyncio.Queue) -> None:
    """把发件箱里的东西依次写出去 —— 每个连接一个协程"""
    while True:
        frame = await outbox.get()
        await websocket.send_text(frame)


@router.websocket("/ws/{room}")
async def websocket_endpoint(websocket: WebSocket, room: str):
    if not _valid_room(room):
        await websocket.close(code=1008)          # 1008 = policy violation
        return

    await websocket.accept()
    outbox: asyncio.Queue = asyncio.Queue()
    sender = asyncio.create_task(_drain(websocket, outbox))
    me = None

    def reply(payload: dict) -> None:
        outbox.put_nowait(json.dumps(payload, ensure_ascii=False))

    try:
        while True:
            # 用 receive() 而不是 receive_text()：对端发二进制帧时 receive_text() 会直接抛异常
            # 把整条连接带走。这里自己判类型，非文本帧给个 error 帧就算了。
            event = await websocket.receive()
            if event.get("type") == "websocket.disconnect":
                break
            raw = event.get("text")
            if raw is None:
                reply({"type": "error", "message": "只接受文本帧（JSON）"})
                continue

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                reply({"type": "error", "message": "不是合法 JSON"})
                continue

            if not isinstance(msg, dict):
                reply({"type": "error", "message": "消息必须是 JSON 对象"})
                continue

            kind = msg.get("type")
            req_id = msg.get("id")            # 回执带上它，发起者才知道是哪一条的回执

            # ⚠️ 单条消息处理出错时**只回绝这一条**，绝不能让异常冒出去把连接拆掉。
            # （题目要求"不能崩"，而一条坏消息断开整条长连接正是最容易踩的崩法。）
            try:
                # ① 加入房间（重连后也会再发一次，带上本地最大 seq 请求补齐）
                if kind == "hello":
                    me = presence.touch(client_ip(websocket), room, {
                        "clientId": msg.get("clientId"),
                        "nickname": msg.get("nickname"),
                        "transport": "ws",
                    })
                    presence.set_connected(me["clientId"], True)
                    hub.add_ws(room, outbox)

                    since = int(msg.get("since") or 0)
                    missed = await room_store.since(room, since)
                    reply({
                        "type": "welcome",
                        "id": req_id,
                        "seq": room_store.seq,
                        "messages": missed,               # 题目要求④：新客户端自动同步当前内容
                        "members": presence.list(room),
                    })
                    hub.broadcast_members(room, presence.list(room))
                    print(f"[ws]  {me['nickname']} 加入 {room} · 补齐 {len(missed)} 条 · 连接数 {hub.ws_count(room)}")

                # ② 发消息：唯一走 socket 上行的路径
                elif kind == "send":
                    if me is None:
                        reply({"type": "error", "id": req_id, "message": "还没 hello，先加入房间"})
                        continue
                    payload = msg.get("payload")
                    if not isinstance(payload, dict):
                        reply({"type": "error", "id": req_id, "message": "payload 必须是对象"})
                        continue

                    me["at"] = int(time.time() * 1000)
                    created = await room_store.push(room, payload, me)
                    # 先回执（发起者靠它拿到 seq 立即上屏），再广播给房间内所有人
                    reply({"type": "ack", "id": req_id, "message": created,
                           "seq": created["seq"], "rtt": 0})
                    hub.broadcast_messages(room, [created])
                    hub.broadcast_members(room, presence.list(room))

                # ③ 应用层心跳
                elif kind == "ping":
                    reply({"type": "pong", "id": req_id, "t": int(time.time() * 1000)})

                else:
                    reply({"type": "error", "id": req_id, "message": f"不认识的消息类型：{kind}"})

            except Exception as err:
                # 这条消息处理失败了 → 告诉发起者，连接继续用
                print(f"[ws]  消息处理失败（{kind}）：{err!r}")
                reply({"type": "error", "id": req_id, "message": f"服务端拒绝：{err}"})

    except WebSocketDisconnect:
        pass
    except Exception as err:                       # 连接级别的意外也不能把服务带崩
        print(f"[ws]  连接异常：{err!r}")
    finally:
        # 断开清理：题目提示里明确要求"及时从对应房间的集合中移除"
        sender.cancel()
        hub.remove_ws(room, outbox)
        if me is not None:
            presence.set_connected(me["clientId"], False)
            hub.broadcast_members(room, presence.list(room))
            print(f"[ws]  {me['nickname']} 离开 {room} · 连接数 {hub.ws_count(room)}")
