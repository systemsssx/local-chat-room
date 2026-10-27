"""认证接口：注册 / 登录 / 登出 / 我是谁。

流程（和 README「登录是怎么实现的」一节对应）：
    register/login → 校验 → 写 sessions 表 → Set-Cookie: sid=随机串
    之后每个请求浏览器自动带 Cookie → current_user 依赖查表认出你是谁
    客户端**永远不传 userId** —— 身份只由服务端从会话里查。
"""

import asyncio
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from .. import auth, config, db

router = APIRouter(prefix="/api/auth", tags=["auth"])


class Credentials(BaseModel):
    username: str
    password: str


def _public_user(user: dict) -> dict:
    """对外只暴露这三个字段 —— pass_hash 之类一个都不出去"""
    return {"id": user["id"], "username": user["username"], "createdAt": user["createdAt"]}


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(body: Credentials, response: Response):
    username = auth.validate_username(body.username)
    password = auth.validate_password(body.password)

    if db.find_user(username) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "这个用户名已经有人用了")

    # pbkdf2 要跑 20 万次（约 0.1 秒），必须放到线程里 —— 直接在事件循环里算，
    # 会把所有请求和 SSE 心跳一起卡住。
    pass_hash = await asyncio.to_thread(auth.hash_password, password)
    try:
        user = await asyncio.to_thread(db.create_user, username, pass_hash)
    except sqlite3.IntegrityError as err:
        # 并发注册同名：唯一索引兜底（上面那次查询和这次插入之间可能有人抢先）
        raise HTTPException(status.HTTP_409_CONFLICT, "这个用户名已经有人用了") from err

    auth.start_session(response, user["id"])          # 注册完直接登录，不用再输一遍
    return {"user": _public_user(user)}


@router.post("/login")
async def login(body: Credentials, response: Response):
    username = (body.username or "").strip()
    auth.check_login_allowed(username)

    row = db.find_user(username)
    if row is None:
        auth.note_login_failure(username)
        # 用户名不存在和密码错必须给同一句话：不然等于告诉别人哪些用户名存在
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码不对")

    ok = await asyncio.to_thread(auth.verify_password, body.password or "", row["pass_hash"])
    if not ok:
        auth.note_login_failure(username)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码不对")

    auth.note_login_success(username)
    auth.start_session(response, row["id"])
    return {"user": _public_user(db.get_user(row["id"]))}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response):
    """登出要两边都清：删库里的那一行（票作废）+ 清浏览器的 Cookie"""
    sid = request.cookies.get(config.COOKIE_NAME)
    if sid:
        auth.end_session(sid)
    auth.clear_cookie(response)


@router.get("/me")
async def me(user: dict = Depends(auth.current_user)):
    return {"user": _public_user(user)}
