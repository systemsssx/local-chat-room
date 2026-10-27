"""登录与鉴权。

三件事，各管一段：

  ① **密码怎么存**：pbkdf2-hmac-sha256 + 每个用户独立的随机盐。
     库里存成 `pbkdf2_sha256$<迭代数>$<盐hex>$<哈希hex>`，永不落明文。
     校验用 `hmac.compare_digest`（常量时间比较，防时序侧信道）。

  ② **登录成功发什么**：一个随机 sid（`secrets.token_urlsafe(32)`，256 位）。
     浏览器拿它当 Cookie；**服务端库里只存 sha256(sid)** —— 库被拿走也不能直接冒充登录。

  ③ **之后怎么认人**：`current_user` 依赖 —— 读 Cookie → 查 sessions → 查 users。
     所有需要身份的接口挂上它即可；前端永远不需要传 userId。

注意：Cookie 里装的是"随机串"，不是用户 id。用户 id 是本服务端查出来的，
客户端说了不算 —— 这就是"客户端能改的东西不能当身份"。
"""

import hashlib
import hmac
import re
import secrets
import threading

from fastapi import HTTPException, Request, Response, status

from . import config, db

# ── 用户名 / 密码规则 ─────────────────────────────────

_USERNAME_RE = re.compile(r"^[0-9A-Za-z_\u4e00-\u9fa5-]{2,16}$")


def validate_username(username: str) -> str:
    name = (username or "").strip()
    if not _USERNAME_RE.match(name):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"用户名要 {config.USERNAME_MIN}–{config.USERNAME_MAX} 个字符：中英文、数字、下划线或短横")
    return name


def validate_password(password: str) -> str:
    pw = password or ""
    if len(pw) < config.PASSWORD_MIN:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"密码至少 {config.PASSWORD_MIN} 位")
    if len(pw) > 128:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "密码太长了")
    return pw


# ── ① 密码哈希 ────────────────────────────────────────

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    iterations = config.PBKDF2_ITERATIONS
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(digest.hex(), hash_hex)


# ── ② 会话（名册） ────────────────────────────────────

def hash_sid(sid: str) -> str:
    return hashlib.sha256(sid.encode("utf-8")).hexdigest()


def start_session(response: Response, user_id: int) -> str:
    """发一张新工牌：随机 sid 给浏览器，哈希存库"""
    sid = secrets.token_urlsafe(32)
    db.create_session(hash_sid(sid), user_id, db.now_ms() + config.SESSION_TTL_MS)
    set_cookie(response, sid)
    return sid


def end_session(sid: str) -> None:
    db.delete_session(hash_sid(sid))


def set_cookie(response: Response, sid: str) -> None:
    """Cookie 三件套：HttpOnly（JS 读不到）、SameSite=Lax（挡 CSRF）、Path=/。

    `Secure` 只在 https 下加 —— 局域网 http 演示时加了它，Cookie 直接不落地。
    """
    response.set_cookie(
        key=config.COOKIE_NAME,
        value=sid,
        max_age=config.SESSION_TTL_MS // 1000,
        httponly=True,
        samesite="lax",
        secure=config.COOKIE_SECURE,
        path="/",
    )


def clear_cookie(response: Response) -> None:
    response.delete_cookie(config.COOKIE_NAME, path="/")


# ── ③ 认人 ────────────────────────────────────────────

def _user_from_request(request: Request, response: Response | None) -> dict | None:
    sid = request.cookies.get(config.COOKIE_NAME)
    if not sid:
        return None
    session = db.get_session(hash_sid(sid))
    if session is None:
        return None
    # 滑动续期：快到期了就往后续，正常用的人不会被踢出去；闲置的卡自然失效
    if session["expires_at"] - db.now_ms() < config.SESSION_RENEW_MS:
        db.touch_session(hash_sid(sid), db.now_ms() + config.SESSION_TTL_MS)
        if response is not None:
            set_cookie(response, sid)
    return db.get_user(session["user_id"])


def current_user(request: Request, response: Response) -> dict:
    """需要登录的接口挂这个依赖；没登录一律 401（前端据此弹回登录页）"""
    user = _user_from_request(request, response)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "请先登录")
    return user


def optional_user(request: Request) -> dict | None:
    """给 SSE 这类拿不到 Response 对象的地方用（返回 None 表示没登录）"""
    return _user_from_request(request, None)


# ── 登录限速（内存计数器，够用且不引依赖）──────────────

_lock = threading.Lock()
_fails: dict[str, list] = {}          # username → [失败次数, 锁到什么时候]


def check_login_allowed(username: str) -> None:
    now = db.now_ms()
    with _lock:
        state = _fails.get(username.lower())
    if state and state[1] > now:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            f"失败次数太多，请 {max(1, (state[1] - now) // 1000)} 秒后再试")


def note_login_failure(username: str) -> None:
    now = db.now_ms()
    with _lock:
        state = _fails.setdefault(username.lower(), [0, 0])
        state[0] += 1
        if state[0] >= config.LOGIN_MAX_FAILS:
            state[1] = now + config.LOGIN_LOCK_MS
            state[0] = 0


def note_login_success(username: str) -> None:
    with _lock:
        _fails.pop(username.lower(), None)
