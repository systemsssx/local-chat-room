"""协同剪贴板 · 局域网后端（FastAPI）

装配顺序有讲究：
    1. /uploads 静态目录（上传的图片与文件）
    2. public 静态目录（前端 index.html / main.js）
    3. /api/* 接口
    4. WebSocket 端点挂在同一个 ASGI 应用上 —— 所以 http 和 ws 共用 8000 端口

FastAPI 会自动生成接口文档，开发时可以直接打开 http://localhost:8000/docs 看全部接口。
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, db, websocket
from .rooms import room_store
from .routes import api_routes, net_routes, sse_routes


class Utf8JSONResponse(JSONResponse):
    """JSON 响应显式带上 charset=utf-8。

    JSON 规范默认就是 UTF-8，浏览器也照此处理；但有些老客户端（比如 Windows PowerShell 5.1
    的 Invoke-WebRequest）在没有 charset 时会退回按 latin1 解 —— 中文就全成乱码了。
    显式声明一下，省掉这类调用方的麻烦。
    """

    media_type = "application/json; charset=utf-8"


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    db.close_db()          # 服务关闭时安全断开数据库


def create_app() -> FastAPI:
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)

    db.open_db()           # 打开 SQLite（文件不存在会自动创建 + 建表）
    room_store.init()      # 消息序号接上历史最大值

    app = FastAPI(
        title="协同剪贴板 · 局域网",
        lifespan=lifespan,
        default_response_class=Utf8JSONResponse,
    )

    app.include_router(net_routes.router)    # /api/health、/api/net、连接实况
    app.include_router(api_routes.router)    # /api/snapshot、/api/messages、/api/history、/api/upload
    app.include_router(sse_routes.router)    # /api/sse/:room
    app.include_router(websocket.router)     # /ws/:room

    app.mount("/uploads", StaticFiles(directory=config.UPLOAD_DIR), name="uploads")
    app.mount("/", StaticFiles(directory=config.PUBLIC_DIR, html=True), name="public")

    return app
