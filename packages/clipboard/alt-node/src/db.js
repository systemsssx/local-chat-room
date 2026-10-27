/**
 * src/db.js —— SQLite 持久化
 *
 * 「库」是什么：磁盘上的**一个文件**（data/app.db），不是 CSV，也不是要单独启动的服务。
 * Node 24 内置 node:sqlite，零依赖；整个库就是这个文件，拷走它等于完成备份。
 *
 * 两张表（按题目提示"分建房间当前状态表和历史记录表"）：
 *   rooms    —— 每个房间的**当前内容**（最后一条消息是什么）+ 版本号 + 更新时间
 *   messages —— **每次修改**（历史记录），一条消息一行
 *
 * 图片/文件**不入库**：二进制写在 uploads/ 目录，这里只存 URL 与元信息（库里放大二进制是坏做法）。
 */
import { DatabaseSync } from 'node:sqlite';
import fs from 'node:fs';
import path from 'node:path';
import { config } from './config.js';

let db = null;

/** 打开（或创建）数据库并建表。返回 db 实例。 */
export function openDatabase(file = path.join(config.dataDir, 'app.db')) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  db = new DatabaseSync(file);

  db.exec(`
    -- WAL：读写并发更好、崩溃恢复更安全（会多出 -wal / -shm 两个辅助文件，正常现象）
    PRAGMA journal_mode = WAL;
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

    -- 按 (room, seq) 查"某房间 seq 之后的消息"是最常用的查询，建索引
    CREATE INDEX IF NOT EXISTS idx_messages_room_seq ON messages(room, seq);
    CREATE INDEX IF NOT EXISTS idx_messages_at       ON messages(at);
  `);

  return db;
}

export function closeDatabase() {
  try { db?.close(); } catch { /* 已关闭 */ }
  db = null;
}

/* ---------- 行 ↔ 消息对象 ---------- */

function rowToMessage(r) {
  if (!r) return null;
  return {
    seq: r.seq,
    id: r.id,
    room: r.room,
    clientId: r.client_id,
    nickname: r.nickname,
    ip: r.ip,
    type: r.type,
    text: r.text ?? '',
    file: r.file_json ? JSON.parse(r.file_json) : undefined,
    source: r.source,
    transport: r.transport,
    at: r.at,
  };
}

/* ---------- 对外接口：就这几件事 ---------- */

export const dbApi = {
  /** 服务启动时：拿到历史最大 seq，**不能让 seq 重置**（前端靠它去重、补差额） */
  maxSeq() {
    return db.prepare('SELECT COALESCE(MAX(seq), 0) AS m FROM messages').get().m;
  },

  /** 写入一条消息 + 更新房间当前状态（一个事务，要么都成要么都不成） */
  saveMessage(msg) {
    db.exec('BEGIN');
    try {
      db.prepare(`
        INSERT OR IGNORE INTO messages
          (id, room, seq, at, type, text, file_json, client_id, nickname, ip, source, transport)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `).run(
        msg.id, msg.room, msg.seq, msg.at, msg.type,
        msg.text ?? '', msg.file ? JSON.stringify(msg.file) : null,
        msg.clientId ?? null, msg.nickname ?? null, msg.ip ?? null,
        msg.source ?? null, msg.transport ?? null,
      );

      // 房间当前内容 = 最新那条消息的可读文本（图片/文件则记文件名）
      const current = msg.text || msg.file?.name || '';
      db.prepare(`
        INSERT INTO rooms (room, text, seq, updated_at, updated_by)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(room) DO UPDATE SET
          text       = excluded.text,
          seq        = excluded.seq,
          updated_at = excluded.updated_at,
          updated_by = excluded.updated_by
      `).run(msg.room, current, msg.seq, msg.at, msg.nickname ?? null);

      db.exec('COMMIT');
    } catch (err) {
      db.exec('ROLLBACK');
      throw err;
    }
  },

  /** 某房间最近 limit 条消息（倒序）—— 右侧历史面板用，**直接查库**，所以跨重启还在 */
  recentMessages(room, limit = config.historyLimit) {
    return db.prepare(
      'SELECT * FROM messages WHERE room = ? ORDER BY seq DESC LIMIT ?'
    ).all(room, limit).map(rowToMessage);
  },

  /** 某房间最早的 limit 条之后的全部消息（用于启动后把房间载入内存） */
  lastMessages(room, limit = config.historyLimit) {
    return db.prepare(
      'SELECT * FROM (SELECT * FROM messages WHERE room = ? ORDER BY seq DESC LIMIT ?) ORDER BY seq ASC'
    ).all(room, limit).map(rowToMessage);
  },

  /** 房间当前状态（题目拓展任务①：保存每个房间当前的剪贴板内容） */
  getRoom(room) {
    return db.prepare('SELECT * FROM rooms WHERE room = ?').get(room) ?? null;
  },

  /** 库里有哪些房间（服务重启后恢复用） */
  allRooms() {
    return db.prepare('SELECT * FROM rooms ORDER BY updated_at DESC').all();
  },

  /* ---------- 统计：给 /api/health 和界面看 ---------- */
  stats() {
    const messages = db.prepare('SELECT COUNT(*) AS c FROM messages').get().c;
    const rooms = db.prepare('SELECT COUNT(*) AS c FROM rooms').get().c;
    let sizeKB = 0;
    try { sizeKB = Math.round(fs.statSync(db.location?.() ?? path.join(config.dataDir, 'app.db')).size / 1024); } catch { /* ignore */ }
    return { messages, rooms, sizeKB, file: 'data/app.db' };
  },
};

export { dbApi as db };
