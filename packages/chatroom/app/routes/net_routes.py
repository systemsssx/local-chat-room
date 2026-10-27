"""局域网相关的接口。

前端靠 /api/net 知道"手机该访问哪个地址"，从而把它显示出来（右栏「服务地址」、开屏提示）。
服务只监听 0.0.0.0 是不够的 —— 用户得知道要输哪个 IP。
"""

import time

from fastapi import APIRouter, Depends

from .. import auth, config, db, net
from ..rooms import hub, require_member

router = APIRouter(tags=["net"])
START_TIME = time.time()


@router.get("/api/health")
async def health():
    """存活探针：给运维和脚本用。

    只回一句"活着"和跑了多久 —— 进程号、库里的账号/消息数量这些是内部信息，
    不该让一个没登录的人随手查到。
    """
    return {"ok": True, "uptimeSec": round(time.time() - START_TIME)}


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


@router.get("/api/rooms/{room_id}/connections")
async def connections(room_id: int, user: dict = Depends(auth.current_user)):
    """某个房间的连接实况（验收 / 排查时用得上）"""
    require_member(room_id, user["id"])
    return {
        "roomId": room_id,
        "sse": len(hub.online_ids(room_id)),
        "online": sorted(hub.online_ids(room_id)),
        "messages": db.latest_seq(room_id),
    }
