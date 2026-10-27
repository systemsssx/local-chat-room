"""消息接口：快照 / 发消息 / 翻页 / 上传。

上行永远是普通 HTTP POST（SSE 只管下行，它本来就不支持浏览器→服务器）。
每个接口的**第一件事**都是确认"你是不是这个房间的成员" —— 这一步漏一个就是越权读别人的群。
"""

import uuid
from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from .. import auth, config, db
from ..rooms import post_message, require_member, room_state, room_store

router = APIRouter(tags=["messages"])


# ── 小工具 ────────────────────────────────────────────

_TEXTUAL_MIME = {"application/json", "application/octet-stream", "application/x-empty", ""}


def is_image(mime: str, ext: str) -> bool:
    return mime in config.IMAGE_MIME or ext in config.IMAGE_EXT


def is_text(mime: str, ext: str) -> bool:
    """纯文本文件的判定：**扩展名白名单说了算**。

    不能只看 MIME —— `text/html`、`image/svg+xml` 都带 "text"/"image" 字样，
    但它们会被浏览器当页面执行。白名单里没有 .html/.svg，就直接进不来。
    （MIME 只在"扩展名过关"之后用于排除明显的伪装，比如 .txt 报成 image/png。）
    """
    if ext not in config.TEXT_EXT:
        return False
    return not mime or mime.startswith("text/") or mime in _TEXTUAL_MIME


def decode_text(data: bytes) -> tuple[str, str]:
    """纯文本解码：先严格按 UTF-8 解，失败再按 GB18030 解。

    Windows 记事本存的 .txt 默认是 ANSI/GBK —— Python 标准库自带 gb18030，不需要第三方库。
    """
    try:
        return data.decode("utf-8"), "UTF-8"
    except UnicodeDecodeError:
        try:
            return data.decode("gb18030"), "GB18030"
        except UnicodeDecodeError:
            return data.decode("latin-1"), "Latin-1"


# ── 房间快照：首屏 / 重连补齐 / 切房间 ──

@router.get("/api/snapshot/{room_id}")
async def snapshot(room_id: int, since: int = 0, user: dict = Depends(auth.current_user)):
    return await room_state(user, room_id, since)


# ── 发消息 ──

@router.post("/api/messages/{room_id}", status_code=status.HTTP_201_CREATED)
async def send(room_id: int, payload: dict | None = Body(default=None),
               user: dict = Depends(auth.current_user)):
    msg = await post_message(user, room_id, payload or {})
    return {"message": msg}


# ── 向上翻页（消息流顶部「加载更早的消息」）──

@router.get("/api/history/{room_id}")
async def history(room_id: int, before: int = 0, limit: int = config.PAGE_LIMIT,
                  user: dict = Depends(auth.current_user)):
    require_member(room_id, user["id"])
    limit = max(1, min(limit, 100))
    if before <= 0:
        messages = await room_store.latest_page(room_id, limit)
    else:
        messages = await room_store.page_before(room_id, before, limit)
    has_more = bool(messages) and messages[0]["seq"] > 1
    return {"messages": messages, "hasMore": has_more}


# ── 上传图片或纯文本文件 ──

@router.post("/api/upload", status_code=status.HTTP_201_CREATED)
async def upload(roomId: int = Form(...), file: UploadFile = File(...),
                 user: dict = Depends(auth.current_user)):
    """返回 file 描述对象，再由前端塞进消息里发出去。

    二进制**写磁盘**（uploads/），库里只存 URL 与元信息 —— 把几 MB 的二进制塞进数据库，
    会让备份变慢、查询变重，而浏览器最终还是要用 URL 去取。
    """
    require_member(roomId, user["id"])          # 不给非成员当免费图床

    ext = Path(file.filename or "").suffix.lower()
    mime = file.content_type or ""
    if not (is_image(mime, ext) or is_text(mime, ext)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "只接受图片或纯文本文件")

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > config.MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"文件超过 {config.MAX_UPLOAD_BYTES // 1024 // 1024}MB")
        chunks.append(chunk)
    data = b"".join(chunks)

    fid = uuid.uuid4().hex[:12]
    stored = f"{fid}{ext}"
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
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

    if kind == "file":                       # 纯文本顺手解一段预览（UTF-8 / GBK 自动识别）
        text, encoding = decode_text(data[: 256 * 1024])
        lines = text.splitlines()
        info["encoding"] = encoding
        info["lines"] = len(lines)
        info["preview"] = "\n".join(lines[:6])[:600]

    # 记一笔"这个文件属于哪个房间" —— 下载时要靠它决定找谁验成员身份
    db.save_upload(stored, roomId, user["id"])
    return {"file": info}


@router.get("/uploads/{filename}")
async def download(filename: str, user: dict = Depends(auth.current_user)):
    """下载上传的图片 / 文件。

    三条规矩，一条都不能省：

      1. **先验成员**：这个文件属于哪个房间，就得先是那个房间的成员
         —— 否则 `uploads/` 就是一个谁都能读的公开图床（连登录都不用）；
      2. **文件名只取 basename**：防 `../` 目录穿越；
      3. **非图片一律当附件发**：`Content-Disposition: attachment` + `text/plain` + `nosniff`
         —— 绝不让浏览器把别人上传的东西**当页面或脚本执行**（那就是同源存储型 XSS，
         对方能直接读走你的 Cookie）。
    """
    name = Path(filename).name                     # 穿越防护
    room_id = db.upload_room(name)
    if room_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文件不存在")
    require_member(room_id, user["id"])            # ★ 这一行是"图床"和"群文件"的分界线

    path = config.UPLOAD_DIR / name
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文件不存在")

    headers = {
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, max-age=3600",
    }
    if path.suffix.lower() in config.IMAGE_EXT:    # 图片可以内联显示
        return FileResponse(path, headers=headers)
    return FileResponse(path, media_type="text/plain; charset=utf-8",
                        content_disposition_type="attachment", filename=name, headers=headers)
