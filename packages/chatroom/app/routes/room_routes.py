"""房间接口：建群 / 凭码入群 / 我的房间列表 / 房间概况 / 退群 / 重置房间码。

进群 = 往 room_members 插一行；"这个人属于这个群"就记在那儿。
所有接口都要登录（`Depends(auth.current_user)`），房间相关的还要是成员。
"""

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from .. import auth, db
from ..rooms import (create_room, dissolve_room, join_room, leave_room, members_view,
                     require_member, reset_code, rooms_payload)

router = APIRouter(prefix="/api/rooms", tags=["rooms"])


class CreateRoomIn(BaseModel):
    name: str
    visibility: str = "private"      # private（只能凭码进）| public（能从公开列表看到）


class JoinRoomIn(BaseModel):
    code: str


# ── 静态路径要写在 /{room_id} 前面，否则 "join" 会被当成房间 id ──

@router.get("")
async def my_rooms(user: dict = Depends(auth.current_user)):
    """我加入的房间（左栏房间列表的全部内容：成员数 / 未读数 / 最后一条）"""
    return {"rooms": rooms_payload(user["id"])}


@router.post("", status_code=status.HTTP_201_CREATED)
async def new_room(body: CreateRoomIn, user: dict = Depends(auth.current_user)):
    room = create_room(user, body.name, body.visibility)
    room["role"] = "owner"
    return {"room": room}


@router.get("/public")
async def public_rooms(q: str = "", user: dict = Depends(auth.current_user)):
    """公开房间（仍然要凭码加入，只是能看到有哪些）"""
    return {"rooms": db.public_rooms(q)}


@router.post("/join")
async def join(body: JoinRoomIn, user: dict = Depends(auth.current_user)):
    room, joined = join_room(user, body.code)
    return {"room": room, "joined": joined}


@router.get("/{room_id}")
async def room_detail(room_id: int, user: dict = Depends(auth.current_user)):
    room = require_member(room_id, user["id"])
    room = dict(room)
    room["members"] = len(db.member_ids(room_id))
    return {"room": room, "members": members_view(room_id)}


@router.post("/{room_id}/leave", status_code=status.HTTP_204_NO_CONTENT)
async def leave(room_id: int, user: dict = Depends(auth.current_user)):
    leave_room(user, room_id)


@router.post("/{room_id}/code")
async def rotate_code(room_id: int, user: dict = Depends(auth.current_user)):
    """重置邀请码：旧码立刻失效（仅房主）"""
    return {"code": reset_code(user, room_id)}


@router.delete("/{room_id}", status_code=status.HTTP_204_NO_CONTENT)
async def dissolve(room_id: int, user: dict = Depends(auth.current_user)):
    """解散房间（仅房主）：连消息一起删"""
    dissolve_room(user, room_id)
