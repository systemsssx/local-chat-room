"""任务二 · SSE（Server-Sent Events）—— 服务器主动推。

一次 GET 请求，响应头是 text/event-stream，服务器**故意不结束这个响应**，
于是可以一直往里写。这条连接上只有"服务器 → 浏览器"一个方向；
浏览器要发东西，仍然走 POST /api/messages。

实现要点：
    每个订阅者一个 asyncio 队列；hub 广播时只往队列里塞字符串，
    这个协程负责 await 队列并 yield 出去 —— 慢客户端不会阻塞广播方。
    队列空闲超过 SSE_PING_SEC 就发一行注释当心跳（长连接太久没数据会被中间设备掐断）。
"""

import asyncio
import json
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from .. import config
from ..rooms import hub, presence, room_store
from .api_routes import client_ip, valid_room

router = APIRouter()


def frame(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/api/sse/{room}")
async def sse(
    room: str,
    request: Request,
    since: int = 0,
    clientId: str = "",
    nickname: str = "",
):
    if not valid_room(room):
        raise HTTPException(status_code=400, detail="房间号不合法")

    client = presence.touch(client_ip(request), room, {
        "clientId": clientId,
        "nickname": nickname,
        "transport": "sse",
    })
    presence.set_connected(client["clientId"], True)

    queue: asyncio.Queue = asyncio.Queue()
    hub.add_sse(room, queue)

    # 连上先补差额（题目要求④：新加入的客户端能获得当前内容）
    missed = await room_store.since(room, since)

    async def event_stream():
        try:
            yield frame("hello", {"seq": room_store.seq, "clientId": client["clientId"]})
            if missed:
                yield frame("messages", {"messages": missed})
            yield frame("members", {"members": presence.list(room)})
            hub.broadcast_members(room, presence.list(room))
            print(f"[sse] {client['nickname']} 进入 {room} · 补齐 {len(missed)} 条 · 订阅者 {hub.sse_count(room)}")

            while True:
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=config.SSE_PING_SEC)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"        # SSE 注释帧 = 心跳
        finally:
            # 题目要求⑤：客户端断开后服务端要清理对应连接
            hub.remove_sse(room, queue)
            presence.set_connected(client["clientId"], False)
            hub.broadcast_members(room, presence.list(room))
            print(f"[sse] {client['nickname']} 离开 {room} · 订阅者 {hub.sse_count(room)}")

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",        # 让 nginx 之类的反代不要缓冲
        },
    )
