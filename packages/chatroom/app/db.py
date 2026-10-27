"""SQLite 持久化（标准库 sqlite3，不装任何东西）。

「库」就是磁盘上的一个文件（data/app.db），拷走它等于完成备份。

五张表：
    users        账号：用户名 + 密码哈希（哈希永不落明文密码）
    sessions     登录会话：sid 的 sha256 → user_id    ← 服务端手里那本"名册"
    rooms        房间实体：房间码 / 房主 / 公开还是私密
    room_members 谁在哪个房间（**进群 = 这里插一行**）+ 已读到第几条
    messages     消息，每行带 room_id

关于"隔离"：
    不是一房一个库、也不是一房一张表，而是**一张表 + 每行带 room_id**，
    隔离靠两件事：① 查询永远带 room_id（走 idx_messages_room_seq）；
    ② 访问之前先 is_member() 确认。数据库本身管不了权限，管权限的是代码。

时间：一律毫秒整数（见 config 顶部说明）。
"""

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path

from . import config

_conn: sqlite3.Connection | None = None
_lock = threading.RLock()          # sqlite3 连接默认不允许跨线程；用锁串行化最省心

SCHEMA = """
PRAGMA journal_mode = WAL;      -- 读写并发更好、崩溃恢复更安全（会多出 -wal/-shm，正常）
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

-- ① 账号
CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username   TEXT    NOT NULL COLLATE NOCASE UNIQUE,
    pass_hash  TEXT    NOT NULL,
    created_at INTEGER NOT NULL
);

-- ② 会话（名册）：只存 sid 的哈希
CREATE TABLE IF NOT EXISTS sessions (
    sid_hash   TEXT    PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    last_seen  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

-- ③ 房间实体
CREATE TABLE IF NOT EXISTS rooms (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT    NOT NULL UNIQUE,          -- 房间码（邀请码）
    name       TEXT    NOT NULL,
    owner_id   INTEGER NOT NULL REFERENCES users(id),
    visibility TEXT    NOT NULL DEFAULT 'private',   -- private | public
    created_at INTEGER NOT NULL
);

-- ④ 成员关系
CREATE TABLE IF NOT EXISTS room_members (
    room_id       INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role          TEXT    NOT NULL DEFAULT 'member',  -- owner | member
    joined_at     INTEGER NOT NULL,
    last_read_seq INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (room_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_members_user ON room_members(user_id);

-- ⑤ 消息（seq 是"房间内"递增）
CREATE TABLE IF NOT EXISTS messages (
    id        TEXT    PRIMARY KEY,
    room_id   INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    seq       INTEGER NOT NULL,
    user_id   INTEGER NOT NULL REFERENCES users(id),
    at        INTEGER NOT NULL,
    type      TEXT    NOT NULL,
    text      TEXT,
    file_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_room_seq ON messages(room_id, seq);
CREATE INDEX IF NOT EXISTS idx_messages_at       ON messages(at);

-- ⑥ 上传记录：文件名 → 属于哪个房间
--    有了它，下载才能先问一句"你是这个房间的成员吗"，
--    否则 uploads/ 就是一个谁都能读的公开图床。
CREATE TABLE IF NOT EXISTS uploads (
    filename  TEXT    PRIMARY KEY,
    room_id   INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    user_id   INTEGER NOT NULL REFERENCES users(id),
    at        INTEGER NOT NULL
);
"""


def now_ms() -> int:
    return int(time.time() * 1000)


# ── 生命周期 ────────────────────────────────────────────

def _is_legacy(path: Path) -> bool:
    """旧库（协同剪贴板版）判定：messages 表有 room 列而不是 room_id。"""
    if not path.exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
        finally:
            conn.close()
    except sqlite3.Error:
        return False
    return bool(cols) and "room" in cols and "room_id" not in cols


def _archive_legacy(path: Path) -> Path:
    """旧结构另存为 .legacy-<时间>.bak，绝不直接删用户的数据。"""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = path.with_suffix(path.suffix + f".legacy-{stamp}.bak")
    path.rename(target)
    for suffix in ("-wal", "-shm"):
        side = Path(str(path) + suffix)
        if side.exists():
            side.rename(Path(str(target) + suffix))
    return target


def open_db(path: Path | None = None) -> sqlite3.Connection:
    global _conn
    if _conn is not None:
        return _conn
    file = Path(path or config.DB_FILE)
    file.parent.mkdir(parents=True, exist_ok=True)
    if _is_legacy(file):
        archived = _archive_legacy(file)
        print(f"  · 检测到旧结构数据库，已另存为 {archived.name}，新建 chat 版数据库")
    _conn = sqlite3.connect(file, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    with _lock:
        _conn.executescript(SCHEMA)
    return _conn


def close_db() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            finally:
                _conn = None


def _db() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("数据库还没打开，先调 open_db()")
    return _conn


# ── 行 → 对象（对外一律 camelCase，和前端契约一致）──────

def _user(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "username": row["username"], "createdAt": row["created_at"]}


def _message(row: sqlite3.Row) -> dict:
    return {
        "seq": row["seq"],
        "id": row["id"],
        "roomId": row["room_id"],
        "userId": row["user_id"],
        "username": row["username"],
        "type": row["type"],
        "text": row["text"] or "",
        "file": json.loads(row["file_json"]) if row["file_json"] else None,
        "at": row["at"],
    }


_MESSAGE_SELECT = """
SELECT m.*, u.username FROM messages m JOIN users u ON u.id = m.user_id
"""


# ══════════════ 账号 ══════════════

def create_user(username: str, pass_hash: str) -> dict:
    with _lock:
        conn = _db()
        with conn:
            cur = conn.execute(
                "INSERT INTO users (username, pass_hash, created_at) VALUES (?, ?, ?)",
                (username, pass_hash, now_ms()),
            )
            uid = cur.lastrowid
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    return _user(row)


def find_user(username: str) -> dict | None:
    """按用户名查（含 pass_hash，登录校验用）"""
    with _lock:
        row = _db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return dict(row) if row else None


def get_user(user_id: int) -> dict | None:
    with _lock:
        row = _db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _user(row) if row else None


# ══════════════ 会话（名册） ══════════════

def create_session(sid_hash: str, user_id: int, expires_at: int) -> None:
    now = now_ms()
    with _lock:
        conn = _db()
        with conn:
            conn.execute(
                "INSERT INTO sessions (sid_hash, user_id, created_at, expires_at, last_seen)"
                " VALUES (?, ?, ?, ?, ?)",
                (sid_hash, user_id, now, expires_at, now),
            )


def get_session(sid_hash: str) -> dict | None:
    """取会话；顺手把过期的那条删掉（惰性清理，不用定时任务）"""
    now = now_ms()
    with _lock:
        conn = _db()
        row = conn.execute("SELECT * FROM sessions WHERE sid_hash = ?", (sid_hash,)).fetchone()
        if row is None:
            return None
        if row["expires_at"] <= now:
            with conn:
                conn.execute("DELETE FROM sessions WHERE sid_hash = ?", (sid_hash,))
            return None
    return dict(row)


def touch_session(sid_hash: str, expires_at: int) -> None:
    """滑动续期：把过期时间往后推，并记下最后一次活动"""
    with _lock:
        conn = _db()
        with conn:
            conn.execute(
                "UPDATE sessions SET expires_at = ?, last_seen = ? WHERE sid_hash = ?",
                (expires_at, now_ms(), sid_hash),
            )


def delete_session(sid_hash: str) -> None:
    with _lock:
        conn = _db()
        with conn:
            conn.execute("DELETE FROM sessions WHERE sid_hash = ?", (sid_hash,))


def delete_sessions_of_user(user_id: int) -> int:
    """改密码 / 强制下线用：一次性踢掉这个人的所有设备"""
    with _lock:
        conn = _db()
        with conn:
            cur = conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    return cur.rowcount


def purge_expired_sessions() -> int:
    with _lock:
        conn = _db()
        with conn:
            cur = conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now_ms(),))
    return cur.rowcount


# ══════════════ 房间 ══════════════

def create_room(code: str, name: str, owner_id: int, visibility: str) -> dict:
    with _lock:
        conn = _db()
        with conn:
            cur = conn.execute(
                "INSERT INTO rooms (code, name, owner_id, visibility, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (code, name, owner_id, visibility, now_ms()),
            )
            room_id = cur.lastrowid
            conn.execute(
                "INSERT INTO room_members (room_id, user_id, role, joined_at, last_read_seq)"
                " VALUES (?, ?, 'owner', ?, 0)",
                (room_id, owner_id, now_ms()),
            )
    room = room_by_id(room_id)
    assert room is not None
    return room


def room_by_id(room_id: int) -> dict | None:
    with _lock:
        row = _db().execute(
            """SELECT r.*, u.username AS owner_name,
                      (SELECT COUNT(*) FROM room_members m WHERE m.room_id = r.id) AS members
               FROM rooms r JOIN users u ON u.id = r.owner_id WHERE r.id = ?""",
            (room_id,),
        ).fetchone()
    return _room(row) if row else None


def room_by_code(code: str) -> dict | None:
    with _lock:
        row = _db().execute(
            """SELECT r.*, u.username AS owner_name,
                      (SELECT COUNT(*) FROM room_members m WHERE m.room_id = r.id) AS members
               FROM rooms r JOIN users u ON u.id = r.owner_id WHERE r.code = ?""",
            (code.strip().upper(),),
        ).fetchone()
    return _room(row) if row else None


def _room(row: sqlite3.Row) -> dict:
    d = {
        "id": row["id"],
        "code": row["code"],
        "name": row["name"],
        "visibility": row["visibility"],
        "ownerId": row["owner_id"],
        "ownerName": row["owner_name"],
        "createdAt": row["created_at"],
    }
    if "members" in row.keys():
        d["members"] = row["members"]
    return d


def set_room_code(room_id: int, code: str) -> None:
    with _lock:
        conn = _db()
        with conn:
            conn.execute("UPDATE rooms SET code = ? WHERE id = ?", (code, room_id))


def set_room_name(room_id: int, name: str) -> None:
    with _lock:
        conn = _db()
        with conn:
            conn.execute("UPDATE rooms SET name = ? WHERE id = ?", (name, room_id))


def delete_room(room_id: int) -> None:
    """删房间（房间没人了就删）。外键 ON DELETE CASCADE 会把成员与消息一起带走。"""
    with _lock:
        conn = _db()
        with conn:
            conn.execute("DELETE FROM rooms WHERE id = ?", (room_id,))


def add_member(room_id: int, user_id: int, role: str = "member") -> bool:
    """进群 = 这里插一行。已经有就返回 False（幂等）"""
    with _lock:
        conn = _db()
        with conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO room_members (room_id, user_id, role, joined_at, last_read_seq)"
                " VALUES (?, ?, ?, ?, (SELECT COALESCE(MAX(seq), 0) FROM messages WHERE room_id = ?))",
                (room_id, user_id, role, now_ms(), room_id),
            )
    return cur.rowcount > 0


def remove_member(room_id: int, user_id: int) -> None:
    with _lock:
        conn = _db()
        with conn:
            conn.execute(
                "DELETE FROM room_members WHERE room_id = ? AND user_id = ?", (room_id, user_id)
            )


def is_member(room_id: int, user_id: int) -> bool:
    """**权限隔离的唯一入口**：访问任何房间数据之前先问它一句"""
    with _lock:
        row = _db().execute(
            "SELECT 1 FROM room_members WHERE room_id = ? AND user_id = ?", (room_id, user_id)
        ).fetchone()
    return row is not None


def member_role(room_id: int, user_id: int) -> str | None:
    with _lock:
        row = _db().execute(
            "SELECT role FROM room_members WHERE room_id = ? AND user_id = ?", (room_id, user_id)
        ).fetchone()
    return row["role"] if row else None


def members_of(room_id: int) -> list[dict]:
    with _lock:
        rows = _db().execute(
            """SELECT m.user_id, u.username, m.role, m.joined_at, m.last_read_seq
               FROM room_members m JOIN users u ON u.id = m.user_id
               WHERE m.room_id = ?
               ORDER BY CASE m.role WHEN 'owner' THEN 0 ELSE 1 END, m.joined_at ASC""",
            (room_id,),
        ).fetchall()
    return [
        {"userId": r["user_id"], "username": r["username"], "role": r["role"],
         "joinedAt": r["joined_at"], "lastReadSeq": r["last_read_seq"]}
        for r in rows
    ]


def member_ids(room_id: int) -> list[int]:
    with _lock:
        rows = _db().execute(
            "SELECT user_id FROM room_members WHERE room_id = ?", (room_id,)
        ).fetchall()
    return [r["user_id"] for r in rows]


def transfer_owner(room_id: int, new_owner_id: int) -> None:
    """房主退群：把房主交给最早加入的那个成员，别让房间变成没人管的孤儿"""
    with _lock:
        conn = _db()
        with conn:
            conn.execute("UPDATE rooms SET owner_id = ? WHERE id = ?", (new_owner_id, room_id))
            conn.execute(
                "UPDATE room_members SET role = 'owner' WHERE room_id = ? AND user_id = ?",
                (room_id, new_owner_id),
            )
            conn.execute(
                "UPDATE room_members SET role = 'member' WHERE room_id = ? AND user_id != ?",
                (room_id, new_owner_id),
            )


def touch_last_read(room_id: int, user_id: int, seq: int) -> None:
    """进房间就把它标记成"已读到这儿"，未读数由此归零"""
    with _lock:
        conn = _db()
        with conn:
            conn.execute(
                "UPDATE room_members SET last_read_seq = MAX(last_read_seq, ?)"
                " WHERE room_id = ? AND user_id = ?",
                (seq, room_id, user_id),
            )


def rooms_of_user(user_id: int) -> list[dict]:
    """我加入的房间（房间列表接口的全部内容）：成员数、最后一条、未读数"""
    with _lock:
        rows = _db().execute(
            """
            SELECT r.id, r.code, r.name, r.visibility, r.created_at,
                   r.owner_id, o.username AS owner_name,
                   m.role, m.joined_at, m.last_read_seq,
                   (SELECT COUNT(*) FROM room_members x WHERE x.room_id = r.id) AS members,
                   (SELECT COALESCE(MAX(g.seq), 0) FROM messages g WHERE g.room_id = r.id) AS last_seq,
                   (SELECT g.at   FROM messages g WHERE g.room_id = r.id ORDER BY g.seq DESC LIMIT 1) AS last_at,
                   (SELECT g.type FROM messages g WHERE g.room_id = r.id ORDER BY g.seq DESC LIMIT 1) AS last_type,
                   (SELECT g.text FROM messages g WHERE g.room_id = r.id ORDER BY g.seq DESC LIMIT 1) AS last_text,
                   (SELECT u2.username FROM messages g JOIN users u2 ON u2.id = g.user_id
                     WHERE g.room_id = r.id ORDER BY g.seq DESC LIMIT 1) AS last_user
            FROM room_members m
            JOIN rooms r ON r.id = m.room_id
            JOIN users o ON o.id = r.owner_id
            WHERE m.user_id = ?
            ORDER BY COALESCE(last_at, r.created_at) DESC, r.id DESC
            """,
            (user_id,),
        ).fetchall()
    return [_room_row(r) for r in rows]


def _room_row(row: sqlite3.Row) -> dict:
    last_seq = row["last_seq"] or 0
    unread = max(0, last_seq - (row["last_read_seq"] or 0))
    preview = row["last_text"] or ""
    if row["last_type"] == "image":
        preview = "[图片]"
    elif row["last_type"] == "file":
        preview = "[文件]"
    if preview and row["last_user"]:
        preview = f"{row['last_user']}：{preview}"
    return {
        "id": row["id"],
        "code": row["code"],
        "name": row["name"],
        "visibility": row["visibility"],
        "ownerId": row["owner_id"],
        "ownerName": row["owner_name"],
        "role": row["role"],
        "createdAt": row["created_at"],
        "members": row["members"],
        "lastSeq": last_seq,
        "unread": unread,
        "lastAt": row["last_at"],
        "lastText": preview,
    }


def _escape_like(text: str) -> str:
    """LIKE 里的 % 和 _ 是通配符：用户搜「100%」不该匹配到所有房间。"""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def public_rooms(keyword: str = "") -> list[dict]:
    """公开房间：**只列房间名与规模，不给房间码** —— 想加入还是得问群主要码。

    列表里带码的话，"凭码入群"对公开房间就成了一句空话（谁都能从列表里抄走）。
    """
    like = f"%{_escape_like(keyword.strip())}%"
    with _lock:
        rows = _db().execute(
            """SELECT r.*, u.username AS owner_name,
                      (SELECT COUNT(*) FROM room_members m WHERE m.room_id = r.id) AS members
               FROM rooms r JOIN users u ON u.id = r.owner_id
               WHERE r.visibility = 'public' AND r.name LIKE ? ESCAPE '\\'
               ORDER BY r.created_at DESC LIMIT 50""",
            (like,),
        ).fetchall()
    out = []
    for row in rows:
        room = _room(row)
        room.pop("code", None)          # ★ 不泄露房间码
        out.append(room)
    return out


# ══════════════ 消息 ══════════════

def append_message(room_id: int, user_id: int, msg_type: str, text: str,
                   file_obj: dict | None, msg_id: str) -> int:
    """写一条消息。seq 在事务里算 —— 并发下也不会重复。返回 seq。

    （seq 是"房间内"递增，前端靠它在房间内去重与补差额。）
    """
    file_json = json.dumps(file_obj, ensure_ascii=False) if file_obj else None
    with _lock:
        conn = _db()
        with conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM messages WHERE room_id = ?",
                (room_id,),
            ).fetchone()
            seq = int(row["n"])
            conn.execute(
                "INSERT INTO messages (id, room_id, seq, user_id, at, type, text, file_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (msg_id, room_id, seq, user_id, now_ms(), msg_type, text, file_json),
            )
    return seq


def message_by_id(msg_id: str) -> dict | None:
    with _lock:
        row = _db().execute(_MESSAGE_SELECT + " WHERE m.id = ?", (msg_id,)).fetchone()
    return _message(row) if row else None


def messages_since(room_id: int, since_seq: int, limit: int | None = None) -> list[dict]:
    sql = _MESSAGE_SELECT + " WHERE m.room_id = ? AND m.seq > ? ORDER BY m.seq ASC"
    args: list = [room_id, since_seq]
    if limit:
        sql += " LIMIT ?"
        args.append(limit)
    with _lock:
        rows = _db().execute(sql, args).fetchall()
    return [_message(r) for r in rows]


def messages_before(room_id: int, before_seq: int, limit: int = config.PAGE_LIMIT) -> list[dict]:
    """向上翻页：取 seq 小于 before_seq 的最近 limit 条（返回升序）"""
    with _lock:
        rows = _db().execute(
            """SELECT * FROM (SELECT m.*, u.username FROM messages m JOIN users u ON u.id = m.user_id
                              WHERE m.room_id = ? AND m.seq < ? ORDER BY m.seq DESC LIMIT ?)
               ORDER BY seq ASC""",
            (room_id, before_seq, limit),
        ).fetchall()
    return [_message(r) for r in rows]


def recent_messages(room_id: int, limit: int = config.HISTORY_LIMIT) -> list[dict]:
    """最近 limit 条（倒序查、升序返回）"""
    with _lock:
        rows = _db().execute(
            """SELECT * FROM (SELECT m.*, u.username FROM messages m JOIN users u ON u.id = m.user_id
                              WHERE m.room_id = ? ORDER BY m.seq DESC LIMIT ?)
               ORDER BY seq ASC""",
            (room_id, limit),
        ).fetchall()
    return [_message(r) for r in rows]


def latest_seq(room_id: int) -> int:
    with _lock:
        row = _db().execute(
            "SELECT COALESCE(MAX(seq), 0) AS n FROM messages WHERE room_id = ?", (room_id,)
        ).fetchone()
    return int(row["n"])


# ══════════════ 统计 ══════════════

def stats() -> dict:
    """给 /api/health 和启动横幅用"""
    with _lock:
        conn = _db()
        messages = conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
        rooms = conn.execute("SELECT COUNT(*) AS c FROM rooms").fetchone()["c"]
        users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    try:
        size_kb = round(config.DB_FILE.stat().st_size / 1024)
    except OSError:
        size_kb = 0
    return {"messages": messages, "rooms": rooms, "users": users,
            "sizeKB": size_kb, "file": "data/app.db"}


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ══════════════ 上传记录 ══════════════

def save_upload(filename: str, room_id: int, user_id: int) -> None:
    with _lock:
        conn = _db()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO uploads (filename, room_id, user_id, at) VALUES (?, ?, ?, ?)",
                (filename, room_id, user_id, now_ms()),
            )


def upload_room(filename: str) -> int | None:
    """这个文件属于哪个房间？下载前用它决定该找谁验成员身份。"""
    with _lock:
        row = _db().execute(
            "SELECT room_id FROM uploads WHERE filename = ?", (filename,)
        ).fetchone()
    return int(row["room_id"]) if row else None
