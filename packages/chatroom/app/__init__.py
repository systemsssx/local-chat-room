"""聊天室后端（FastAPI）—— 装配入口。

装配顺序有讲究：
    1. /uploads 静态目录（上传的图片与文件）
    2. public 静态目录（前端页面）
    3. /api/* 接口
    4. SSE 挂在同一个 ASGI 应用上 —— 所以页面和推送共用 8000 端口，不用配跨域

接口文档（FastAPI 自动生成）：http://localhost:8000/docs
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, db
from .routes import api_routes, auth_routes, net_routes, room_routes, sse_routes


class Utf8JSONResponse(JSONResponse):
    """JSON 响应显式带上 charset=utf-8。

    JSON 规范默认就是 UTF-8，浏览器也照此处理；但有些老客户端（比如 PowerShell 5.1
    的 Invoke-WebRequest）在没有 charset 时会退回按 latin1 解 —— 中文就全成乱码了。
    """

    media_type = "application/json; charset=utf-8"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.purge_expired_sessions()      # 启动时清一次过期的会话（顺便清理老数据）
    yield
    db.close_db()


def create_app() -> FastAPI:
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)

    db.open_db()                     # 打开 SQLite（文件不存在会自动创建 + 建表）

    app = FastAPI(
        title="聊天室",
        lifespan=lifespan,
        default_response_class=Utf8JSONResponse,
    )

    @app.middleware("http")
    async def revalidate_frontend(request, call_next):
        """页面 / JS / CSS 一律要求浏览器**先回来问一句**再用缓存。

        为什么需要：改完前端最气人的就是"我明明改了，浏览器打开还是旧页面"，
        而且旧页面还会拿着旧接口去打新服务端（表现是一堆莫名其妙的 404）。
        加了 `no-cache` 之后浏览器仍会用 ETag 做条件请求：没变就 304（省流量），
        变了就立刻拿新的 —— 既不会用错版本，也不会白下载。
        """
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.endswith((".html", ".js", ".css")):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

    app.include_router(net_routes.router)     # /api/health、/api/net
    app.include_router(auth_routes.router)    # /api/auth/*
    app.include_router(room_routes.router)    # /api/rooms/*
    app.include_router(api_routes.router)     # /api/snapshot、/api/messages、/api/history、/api/upload、/uploads/{name}
    app.include_router(sse_routes.router)     # /api/sse

    # 注意：**没有** StaticFiles 挂 /uploads。
    # 上传的文件必须经过"你是不是这个房间的成员"这一关，见 api_routes.download()。
    app.mount("/", StaticFiles(directory=config.PUBLIC_DIR, html=True), name="public")

    return app
