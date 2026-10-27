"""局域网相关的接口。

前端靠 /api/net 知道"手机该访问哪个地址"，从而把它显示出来（右栏「服务地址」、开屏提示）。
服务只监听 0.0.0.0 是不够的 —— 用户得知道要输哪个 IP。
"""

import os
import time

from fastapi import APIRouter

from .. import config, db, net
from ..rooms import hub, presence, room_store

router = APIRouter()
START_TIME = time.time()


@router.get("/api/health")
async def health():
    """健康检查：给运维/脚本用，也给界面显示数据库实况"""
    return {
        "ok": True,
        "pid": os.getpid(),
        "uptimeSec": round(time.time() - START_TIME),
        "msgs": room_store.seq,
        "db": db.stats(),
    }


@router.get("/api/net")
async def net_info():
    """网络信息：本机地址、局域网地址、是否对外开放"""
    info = net.access_info(config.HOST, config.PORT)
    if not info["lanOpen"]:
        info["warning"] = f"当前只监听本机（HOST={config.HOST}），局域网内其它设备连不上"
    elif not info["addresses"]:
        info["warning"] = "没有找到可用的局域网 IPv4 地址（网卡没连上？）"
    else:
        info["warning"] = None
    return info


@router.get("/api/rooms/{room}/connections")
async def connections(room: str):
    """某个房间的连接实况（截图 / 验收时用得上）"""
    return {
        "room": room,
        "sse": hub.sse_count(room),
        "ws": hub.ws_count(room),
        "members": len(presence.list(room)),
        "messages": len(await room_store.since(room, 0)),
    }
