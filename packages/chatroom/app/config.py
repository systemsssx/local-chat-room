"""配置集中在这里 —— 改一处全局生效。

启动方式：
    python server.py                  默认 0.0.0.0:8000（局域网可见）
    python server.py --port 9000      换端口
    HOST=127.0.0.1 python server.py   只监听本机（局域网连不上，仅调试）

时间单位：**全项目统一用毫秒**（`int(time.time() * 1000)`）——
消息时间、会话过期时间都一样。混用秒和毫秒是 bug 温床，所以定死一套。
"""

import os
import sys
from pathlib import Path


def _resource_root() -> Path:
    """只读资源目录（public/）。打包成 exe 后资源在 sys._MEIPASS。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def _writable_root() -> Path:
    """可写目录（data/ uploads/）—— 必须放在 exe 旁边，临时目录会在退出时被删。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return _resource_root()


RESOURCE_ROOT = _resource_root()
WRITABLE_ROOT = _writable_root()

# ── 监听 ──────────────────────────────────────────────
HOST = os.environ.get("HOST", "0.0.0.0")   # 0.0.0.0 = 所有网卡（局域网能连）
PORT = int(os.environ.get("PORT", "8000"))

# ── 目录 ──────────────────────────────────────────────
PUBLIC_DIR = RESOURCE_ROOT / "public"
UPLOAD_DIR = WRITABLE_ROOT / "uploads"
DATA_DIR = WRITABLE_ROOT / "data"
DB_FILE = DATA_DIR / "app.db"

# ── 会话 / 鉴权 ───────────────────────────────────────
COOKIE_NAME = "sid"
SESSION_TTL_MS = 7 * 24 * 60 * 60 * 1000        # 会话有效期 7 天
SESSION_RENEW_MS = 3 * 24 * 60 * 60 * 1000      # 剩余不足 3 天自动续到 7 天（滑动续期）
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"   # 上 https 才置 1；局域网 http 必须 0
PBKDF2_ITERATIONS = int(os.environ.get("PBKDF2_ITERATIONS", "200000"))

USERNAME_MIN, USERNAME_MAX = 2, 16
PASSWORD_MIN = 8
LOGIN_MAX_FAILS = 5          # 同一用户名连续失败几次就锁
LOGIN_LOCK_MS = 60 * 1000    # 锁多久

# ── 房间 ──────────────────────────────────────────────
ROOM_CODE_LEN = 6
ROOM_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # 去掉 0 O 1 I L（念不清）
ROOM_NAME_MAX = 24
ROOM_CODE_RETRY = 12         # 房间码撞了就重试几次
ROOM_MEMBERS_MAX = 200

# ── 限制（对大小和类型进行合理限制）────────────────────
MAX_UPLOAD_BYTES = 2 * 1024 * 1024      # 单个上传文件 2MB
MAX_TEXT_BYTES = 64 * 1024              # 单条文字 64KB

IMAGE_MIME = {"image/png", "image/jpeg", "image/gif", "image/webp"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
# 允许上传的纯文本文件。
# ⚠️ 这里**故意不收** .html / .htm / .svg / .xml：它们会被浏览器当页面/脚本执行，
#   上传到同源目录就是存储型 XSS —— 别人打开下载链接就等于在我们的源里跑他的脚本。
#   少收几种文件格式，比事后加一百个响应头都省事。
TEXT_EXT = {
    ".txt", ".md", ".py", ".js", ".mjs", ".json", ".css", ".csv",
    ".log", ".yml", ".yaml", ".ini", ".conf", ".sh", ".c", ".cpp", ".java",
    ".go", ".rs", ".sql", ".ts",
}

# ── 连接与心跳 ────────────────────────────────────────
SSE_PING_SEC = 20          # SSE 心跳间隔（防止长连接被中间设备掐断）
HISTORY_LIMIT = 20         # 首屏加载条数
PAGE_LIMIT = 30            # 向上翻页每页条数
MEMORY_MESSAGES = 500      # 每个房间在内存里保留多少条（更早的仍在库里）


def is_lan_open() -> bool:
    """是否对局域网开放 —— 决定启动横幅和 /api/net 的提示怎么写"""
    return HOST in ("0.0.0.0", "::")
