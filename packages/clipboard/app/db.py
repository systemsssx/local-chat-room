"""SQLite 持久化（用 Python 标准库 sqlite3，不装任何东西）。

「库」是什么：磁盘上的**一个文件**（data/app.db），不是 CSV，也不是要单独启动的服务。
文件内部是 B-Tree + 页结构；拷走这个文件等于完成备份。

两张表（按题目提示"分建房间当前状态表和历史记录表"）：
    rooms    —— 每个房间的当前内容 + 版本号 + 更新时间 + 最后修改者
    messages —— 每次修改（历史记录），一条消息一行

图片/文件**不入库**：二进制写在 uploads/ 目录，这里只存 URL 与元信息。
"""

import sqlite3
import threading
from pathlib import Path

from . import config

_conn: sqlite3.Connection | None = None
_lock = threading.RLock()          # sqlite3 连接默认不允许跨线程；用锁串行化最省心

SCHEMA = """
PRAGMA journal_mode = WAL;      -- 读写并发更好、崩溃恢复更安全（会多出 -wal/-shm，正常）
PRAGMA synchronous = NORMAL;

-- ① 房间当前状态表：一个房间一行
CREATE TABLE IF NOT EXISTS rooms (
    room        TEXT PRIMARY KEY,
    text        TEXT    NOT NULL DEFAULT '',
    seq         INTEGER NOT NULL DEFAULT 0,
    updated_at  INTEGER NOT NULL,
    updated_by  TEXT
);

-- ② 历史记录表：每次修改一行（也就是每条消息）
CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    room        TEXT    NOT NULL,
    seq         INTEGER NOT NULL,
    at          INTEGER NOT NULL,
    type        TEXT    NOT NULL,
    text        TEXT,
    file_json   TEXT,
    client_id   TEXT,
    nickname    TEXT,
    ip          TEXT,
    source      TEXT,
    transport   TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_room_seq ON messages(room, seq);
CREATE INDEX IF NOT EXISTS idx_messages_at       ON messages(at);
"""


# ── 生命周期 ────────────────────────────────────────────

def open_db(path: Path | None = None) -> sqlite3.Connection:
    global _conn
    if _conn is not None:
        return _conn
    file = Path(path or config.DB_FILE)
    file.parent.mkdir(parents=True, exist_ok=True)
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


# ── 行 → 消息对象（对外一律 camelCase，和前端契约一致）──

def _row_to_message(row: sqlite3.Row) -> dict:
    import json
    return {
        "seq": row["seq"],
        "id": row["id"],
        "room": row["room"],
        "clientId": row["client_id"],
        "nickname": row["nickname"],
        "ip": row["ip"],
        "type": row["type"],
        "text": row["text"] or "",
        "file": json.loads(row["file_json"]) if row["file_json"] else None,
        "source": row["source"],
        "transport": row["transport"],
        "at": row["at"],
    }


# ── 对外接口 ────────────────────────────────────────────

def max_seq() -> int:
    with _lock:
        row = _db().execute("SELECT COALESCE(MAX(seq), 0) AS m FROM messages").fetchone()
        return int(row["m"])


def save_message(msg: dict) -> None:
    """写入一条消息 + 更新房间当前状态（一个事务，要么都成要么都不成）"""
    import json
    file_json = json.dumps(msg["file"], ensure_ascii=False) if msg.get("file") else None
    current = msg.get("text") or (msg.get("file") or {}).get("name") or ""

    with _lock:
        conn = _db()
        with conn:                                  # with conn = 事务（异常自动回滚）
            conn.execute(
                """INSERT OR IGNORE INTO messages
                   (id, room, seq, at, type, text, file_json,
                    client_id, nickname, ip, source, transport)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (msg["id"], msg["room"], msg["seq"], msg["at"], msg["type"],
                 msg.get("text") or "", file_json,
                 msg.get("clientId"), msg.get("nickname"), msg.get("ip"),
                 msg.get("source"), msg.get("transport")),
            )
            conn.execute(
                """INSERT INTO rooms (room, text, seq, updated_at, updated_by)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(room) DO UPDATE SET
                       text       = excluded.text,
                       seq        = excluded.seq,
                       updated_at = excluded.updated_at,
                       updated_by = excluded.updated_by""",
                (msg["room"], current, msg["seq"], msg["at"], msg.get("nickname")),
            )


def recent_messages(room: str, limit: int = config.HISTORY_LIMIT) -> list[dict]:
    """某房间最近 limit 条（倒序）—— 右侧历史面板用，直接查库，所以跨重启还在"""
    with _lock:
        rows = _db().execute(
            "SELECT * FROM messages WHERE room = ? ORDER BY seq DESC LIMIT ?", (room, limit)
        ).fetchall()
    return [_row_to_message(r) for r in rows]


def last_messages(room: str, limit: int = config.MEMORY_MESSAGES) -> list[dict]:
    """某房间最近的 limit 条，按 seq 升序 —— 服务启动后把房间载入内存用"""
    with _lock:
        rows = _db().execute(
            """SELECT * FROM (SELECT * FROM messages WHERE room = ?
                              ORDER BY seq DESC LIMIT ?) ORDER BY seq ASC""",
            (room, limit),
        ).fetchall()
    return [_row_to_message(r) for r in rows]


def get_room(room: str) -> dict | None:
    """房间当前状态（题目拓展任务①：保存每个房间当前的剪贴板内容）"""
    with _lock:
        row = _db().execute("SELECT * FROM rooms WHERE room = ?", (room,)).fetchone()
    return dict(row) if row else None


def all_rooms() -> list[dict]:
    with _lock:
        return [dict(r) for r in _db().execute("SELECT * FROM rooms ORDER BY updated_at DESC").fetchall()]


def stats() -> dict:
    """给 /api/health 和界面用"""
    with _lock:
        messages = _db().execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
        rooms = _db().execute("SELECT COUNT(*) AS c FROM rooms").fetchone()["c"]
    try:
        size_kb = round(config.DB_FILE.stat().st_size / 1024)
    except OSError:
        size_kb = 0
    return {"messages": messages, "rooms": rooms, "sizeKB": size_kb, "file": "data/app.db"}
