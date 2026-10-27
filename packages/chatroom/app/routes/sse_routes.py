"""SSE —— 项目里**唯一**的实时通道（服务器主动推）。

一次 GET 请求，响应头 `text/event-stream`，服务器故意不结束这个响应，于是可以一直往里写。
这条连接上只有"服务器 → 浏览器"一个方向；浏览器要发东西仍然走 POST /api/messages。

一条连接同时挂在两个频道上：
    · 房间频道 —— 这个房间的新消息、成员/在线变化
    · 用户频道 —— "我的房间列表"变化（被拉进群、被踢、其它房间有新消息）

鉴权：EventSource 不能自定义请求头，但**浏览器会自动带 Cookie**，
所以这里用 Cookie 认人（这正是当初选 Cookie 而不是 Bearer token 的原因）。
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from .. import auth, config, db
from ..rooms import frame, hub, members_view, require_member, room_store, rooms_payload

router = APIRouter(tags=["sse"])


@router.get("/api/sse")
async def sse(request: Request, room: int | None = None, since: int = 0):
    user = auth.optional_user(request)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "请先登录")
    if room is not None:
        require_member(room, user["id"])       # 不是成员就别订阅这个房间的推送

    queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    hub.subscribe(room, user["id"], queue)

    # 连上先补差额，再报一次房间与房间列表 —— 前端据此把界面摆正
    missed = await room_store.since(room, since) if room is not None else []
    members = members_view(room) if room is not None else []
    latest = db.latest_seq(room) if room is not None else 0

    async def event_stream():
        try:
            yield frame("hello", {
                "user": {"id": user["id"], "username": user["username"]},
                "roomId": room,
                "members": members,
                "latestSeq": latest,
            })
            if missed:
                yield frame("messages", {"messages": missed})
            yield frame("rooms", {"rooms": rooms_payload(user["id"])})
            if room is not None:                # 我上线了 —— 让同房间的人看到"在线"
                hub.push_room(room, "members", {"members": members_view(room)})

            while True:
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=config.SSE_PING_SEC)
                except asyncio.TimeoutError:
                    yield frame("ping", {})      # 心跳：长连接太久没数据会被中间设备掐断
        finally:
            # 客户端断开（关页面 / 切房间 / 断网）后清理订阅，并通知同房间的人
            hub.unsubscribe(room, user["id"], queue)
            if room is not None:
                hub.push_room(room, "members", {"members": members_view(room)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",        # 让 nginx 之类的反代不要缓冲
        },
    )
