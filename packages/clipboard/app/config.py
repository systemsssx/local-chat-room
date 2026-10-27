"""配置集中在这里 —— 改一处全局生效。

启动方式：
    python server.py               默认 0.0.0.0:8000（局域网可见）
    HOST=127.0.0.1 python server.py  只监听本机（局域网连不上，仅调试）

Windows PowerShell 里设环境变量：
    $env:PORT=9000; python server.py
"""

import os
import sys
from pathlib import Path


def _resource_root() -> Path:
    """只读资源目录（public/）。

    打包成 exe（PyInstaller）之后，资源被解压到一个临时目录，路径在 sys._MEIPASS；
    正常跑源码时就是项目根目录。
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def _writable_root() -> Path:
    """可写目录（data/ uploads/）—— **必须放在 exe 旁边**。

    打包后的临时目录在进程退出时会被删掉，数据写在那儿重启就没了。
    所以 exe 运行时，data/ 和 uploads/ 会出现在 exe 所在的文件夹里。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return _resource_root()


RESOURCE_ROOT = _resource_root()     # 放 public/
WRITABLE_ROOT = _writable_root()     # 放 data/ 和 uploads/

# ── 监听 ──────────────────────────────────────────────
HOST = os.environ.get("HOST", "0.0.0.0")   # 0.0.0.0 = 所有网卡（局域网能连）
PORT = int(os.environ.get("PORT", "8000"))

# ── 目录 ──────────────────────────────────────────────
PUBLIC_DIR = RESOURCE_ROOT / "public"    # 前端
UPLOAD_DIR = WRITABLE_ROOT / "uploads"   # 图片/文件（二进制放磁盘，不进库）
DATA_DIR = WRITABLE_ROOT / "data"        # SQLite 数据库文件
DB_FILE = DATA_DIR / "app.db"

# ── 限制（题目要求"对大小和类型进行合理限制"）──────────
MAX_UPLOAD_BYTES = 2 * 1024 * 1024      # 单个上传文件 2MB
MAX_TEXT_BYTES = 64 * 1024              # 单条文字 64KB

IMAGE_MIME = {"image/png", "image/jpeg", "image/gif", "image/webp"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
TEXT_EXT = {
    ".txt", ".md", ".py", ".js", ".mjs", ".json", ".css", ".html", ".csv",
    ".log", ".yml", ".yaml", ".ini", ".conf", ".sh", ".c", ".cpp", ".java",
    ".go", ".rs", ".sql", ".ts",
}

# ── 连接与心跳 ────────────────────────────────────────
SSE_PING_SEC = 20          # SSE 心跳间隔（防止长连接被中间设备掐断）
WS_PING_SEC = 30           # WebSocket 心跳间隔
MEMBER_TTL_SEC = 15        # 超过这么久没动静就不算"在线"（轮询客户端靠这个判断）

# ── 数据 ──────────────────────────────────────────────
HISTORY_LIMIT = 20         # 题目要求"最近 20 条"
MEMORY_MESSAGES = 500      # 每个房间在内存里保留多少条（更早的仍在库里）


def is_lan_open() -> bool:
    """是否对局域网开放 —— 决定启动横幅和 /api/net 的提示怎么写"""
    return HOST in ("0.0.0.0", "::")
