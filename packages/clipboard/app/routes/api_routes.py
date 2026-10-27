"""任务一 · 普通 HTTP 接口：快照 / 发消息 / 历史 / 上传。

三种传输共用这一套：
    · 轮询：下行靠 GET /api/snapshot，上行 POST /api/messages
    · SSE ：下行靠 /api/sse，上行**仍然是** POST /api/messages
    · WS  ：上下行都走 socket，不走这里
"""

import json
import re
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Body, File, HTTPException, Request, UploadFile

from .. import config
from ..rooms import hub, presence, room_store

router = APIRouter()

_ROOM_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")


# ── 小工具 ────────────────────────────────────────────

def valid_room(room: str) -> bool:
    return bool(_ROOM_RE.fullmatch(room or ""))


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()          # 便于本地模拟多台设备
    return request.client.host if request.client else "127.0.0.1"


def is_image(mime: str, ext: str) -> bool:
    return mime in config.IMAGE_MIME or ext in config.IMAGE_EXT


def is_text(mime: str, ext: str) -> bool:
    # 有些客户端不给 multipart 部分带 Content-Type，所以扩展名也要兜底
    return mime.startswith("text/") or ext in config.TEXT_EXT


def decode_text(data: bytes) -> tuple[str, str]:
    """纯文本解码：先严格按 UTF-8 解，失败再按 GB18030 解。

    Windows 记事本存的 .txt 默认是 ANSI/GBK —— Python 标准库自带 gb18030 编解码器，
    不需要任何第三方库。
    """
    try:
        return data.decode("utf-8"), "UTF-8"
    except UnicodeDecodeError:
        try:
            return data.decode("gb18030"), "GB18030"
        except UnicodeDecodeError:
            return data.decode("latin-1"), "Latin-1"


def _touch(request: Request, room: str, info: dict) -> dict:
    return presence.touch(client_ip(request), room, info)


# ── 房间快照：初始加载 / 切换传输补差额 / 轮询下行，三处共用 ──

@router.get("/api/snapshot/{room}")
async def snapshot(
    room: str,
    request: Request,
    since: int = 0,
    clientId: str = "",
    nickname: str = "",
    transport: str = "polling",
):
    if not valid_room(room):
        raise HTTPException(status_code=400, detail="房间号不合法")

    _touch(request, room, {"clientId": clientId, "nickname": nickname, "transport": transport})
    return {
        "room": room,
        "seq": room_store.seq,
        "messages": await room_store.since(room, since),
        "members": presence.list(room),
        "serverTime": int(time.time() * 1000),
    }


# ── 发消息（轮询版与 SSE 版的上行通道）──

@router.post("/api/messages/{room}")
async def post_message(room: str, request: Request, payload: dict | None = Body(default=None)):
    if not valid_room(room):
        raise HTTPException(status_code=400, detail="房间号不合法")

    body = payload or {}
    client = _touch(request, room, {
        "clientId": body.get("clientId"),
        "nickname": body.get("nickname"),
        "transport": body.get("transport"),
    })
    try:
        # 大小/类型校验统一在 room_store.push 里做（WS 那条路共用同一套规则）
        msg = await room_store.push(room, body, client)
    except ValueError as err:
        raise HTTPException(status_code=413, detail=str(err))
    hub.broadcast_messages(room, [msg])
    hub.broadcast_members(room, presence.list(room))
    return {"ok": True, "message": msg}


# ── 历史记录（直接查 SQLite）──

@router.get("/api/history/{room}")
async def history(room: str, limit: int = config.HISTORY_LIMIT):
    if not valid_room(room):
        raise HTTPException(status_code=400, detail="房间号不合法")

    limit = max(1, min(limit, 50))
    msgs = await room_store.history(room, limit)
    items = [{
        "seq": m["seq"],
        "at": m["at"],
        "type": m["type"],
        "text": m["text"] or "",
        "label": (m["text"] or (m.get("file") or {}).get("name") or "(空)")[:40],
    } for m in msgs]
    return {"room": room, "items": items}


# ── 上传图片或纯文本文件 ──

@router.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    """返回 file 描述对象，再由前端塞进消息里发出去。

    二进制**写磁盘**（uploads/），库里只存 URL 与元信息 —— 这是标准做法：
    把几 MB 的二进制塞进数据库，会让备份变慢、查询变重，而浏览器最终还是要用 URL 去取。
    """
    ext = Path(file.filename or "").suffix.lower()
    mime = file.content_type or ""
    if not (is_image(mime, ext) or is_text(mime, ext)):
        raise HTTPException(status_code=400, detail="只接受图片或纯文本文件")

    # 分块读并累计大小：超过上限立刻拒绝，不把整个文件读进内存
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > config.MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"文件超过 {config.MAX_UPLOAD_BYTES // 1024 // 1024}MB",
            )
        chunks.append(chunk)
    data = b"".join(chunks)

    fid = uuid.uuid4().hex[:12]
    stored = f"{fid}{ext}"
    (config.UPLOAD_DIR / stored).write_bytes(data)

    kind = "image" if is_image(mime, ext) else "file"
    info = {
        "id": fid,
        "url": f"/uploads/{stored}",
        "name": file.filename,
        "size": total,
        "mime": mime,
        "kind": kind,
    }

    # 纯文本文件：顺手解码并给一段预览（UTF-8 / GBK 自动识别）
    if kind == "file":
        text, encoding = decode_text(data[: 256 * 1024])
        lines = text.splitlines()
        info["encoding"] = encoding
        info["lines"] = len(lines)
        info["preview"] = "\n".join(lines[:6])[:600]

    print(f"[upload] {file.filename} → {stored} ({total}B, {kind})")
    return {"ok": True, "file": info}
