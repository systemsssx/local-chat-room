/**
 * src/rooms.js —— 房间数据 / 连接订阅 / 在线成员
 *
 * 拆成三块，因为它们的"寿命"不一样：
 *   · roomStore —— 房间与消息：**内存做热数据 + SQLite 做持久化（写穿）**，接口不变
 *   · hub       —— 连接订阅者（SSE 的 res / WS 的 socket），永远是内存态，重启即断
 *   · presence  —— 谁在线（靠"最近有没有动静"判断，轮询客户端没有长连接只能这样）
 *
 * 持久化策略（write-through）：
 *   启动：SEQ 从库里取最大值（**不能让序号重置**，前端靠它去重和补差额）
 *   首次访问某房间：把该房间最近 N 条消息从库里载入内存
 *   每次发消息：先写库（一个事务：messages + rooms 两张表），再更新内存
 *   读历史：直接查库（跨重启也在）
 */
import { randomUUID } from 'node:crypto';
import { config } from './config.js';
import { db } from './db.js';

/** 每个房间在内存里保留多少条（更早的仍留在库里，只是不进内存） */
const MEMORY_MESSAGES = 500;

/* ══════════════ 1. 房间与消息 ══════════════ */

const rooms = new Map();   // room -> message[]（内存热数据）
let SEQ = 0;               // 单调递增；前端按它去重，切换传输时用它当 since

const ensure = (room) => {
  if (!rooms.has(room)) {
    rooms.set(room, db.lastMessages(room, MEMORY_MESSAGES));
  }
  return rooms.get(room);
};

export const roomStore = {
  /** 服务启动时调用：把序号接上历史最大值 */
  init() {
    SEQ = db.maxSeq();
    return SEQ;
  },

  get seq() { return SEQ; },

  /** 追加一条消息：写库（事务）→ 更新内存 → 返回它 */
  push(room, input, client) {
    const msg = {
      seq: ++SEQ,
      id: randomUUID(),
      room,
      clientId: client.clientId,
      nickname: client.nickname,
      ip: client.ip,
      type: input.type ?? 'text',
      text: input.text ?? '',
      file: input.file ?? undefined,
      source: input.source ?? 'text',       // 哪种输入模式产生的（只做标记）
      transport: client.transport,          // 哪条管道送出的（只做标记）
      at: Date.now(),
    };

    // ⚠️ 顺序要紧：必须**先把房间载入内存**，再落库，最后 push。
    // 反过来的话，冷房间第一次写消息会先落库、后被 ensure() 从库里载入一次，再 push 一次 → 重复。
    const list = ensure(room);
    db.saveMessage(msg);                    // 落库失败就不该出现在窗口里
    list.push(msg);
    return msg;
  },

  /** 某房间在 seq 之后的消息（切换传输补差额 / 轮询下行 / 重连补齐都用它） */
  since(room, seq) {
    return ensure(room).filter((m) => m.seq > seq);
  },

  /** 最近 N 条（倒序），给右侧历史面板 —— 直接查库，所以重启后照样有 */
  history(room, limit = config.historyLimit) {
    return db.recentMessages(room, limit);
  },

  /** 房间数（内存里 + 库里） */
  get roomCount() {
    return Math.max(rooms.size, db.allRooms().length);
  },
};

/* ══════════════ 2. 连接订阅（SSE / WebSocket） ══════════════ */

const sseSubs = new Map();   // room -> Set<res>
const wsSubs = new Map();    // room -> Set<ws>

export const hub = {
  addSse(room, res) { (sseSubs.get(room) ?? sseSubs.set(room, new Set()).get(room)).add(res); },
  removeSse(room, res) { sseSubs.get(room)?.delete(res); },
  addWs(room, ws) { (wsSubs.get(room) ?? wsSubs.set(room, new Set()).get(room)).add(ws); },
  removeWs(room, ws) { wsSubs.get(room)?.delete(ws); },
  sseCount(room) { return sseSubs.get(room)?.size ?? 0; },
  wsCount(room) { return wsSubs.get(room)?.size ?? 0; },

  /** 广播消息：三套传输里只有 SSE 和 WS 需要"推" */
  broadcastMessages(room, messages) {
    const frame = `event: messages\ndata: ${JSON.stringify({ messages })}\n\n`;
    for (const res of sseSubs.get(room) ?? []) {
      try { res.write(frame); } catch { /* 已断开，close 事件里会清理 */ }
    }
    const json = JSON.stringify({ type: 'broadcast', messages });
    for (const ws of wsSubs.get(room) ?? []) {
      if (ws.readyState === 1) ws.send(json);
    }
  },

  /** 在线成员变了，通知该房间所有人 */
  broadcastMembers(room, members) {
    const frame = `event: members\ndata: ${JSON.stringify({ members })}\n\n`;
    for (const res of sseSubs.get(room) ?? []) {
      try { res.write(frame); } catch { /* ignore */ }
    }
    const json = JSON.stringify({ type: 'members', members });
    for (const ws of wsSubs.get(room) ?? []) {
      if (ws.readyState === 1) ws.send(json);
    }
  },
};

/* ══════════════ 3. 在线成员 ══════════════ */

const clients = new Map();   // clientId -> { nickname, ip, transport, at, connected, room }

function clientIp(req) {
  const raw = req?.headers?.['x-forwarded-for']?.split(',')[0] ?? req?.socket?.remoteAddress ?? '';
  return String(raw).replace('::ffff:', '') || '127.0.0.1';
}

export const presence = {
  /** 登记/刷新一个客户端（轮询客户端每次都调，靠"最近见过"判断在线） */
  touch(req, room, info = {}) {
    const id = String(info.clientId || 'anon');
    const prev = clients.get(id) ?? { connected: false };
    const c = {
      ...prev,
      clientId: id,
      room,
      nickname: info.nickname || prev.nickname || '匿名',
      transport: info.transport || prev.transport || 'polling',
      ip: prev.ip || clientIp(req),
      at: Date.now(),
    };
    clients.set(id, c);
    return c;
  },

  setConnected(clientId, connected) {
    const c = clients.get(clientId);
    if (c) { c.connected = connected; if (!connected) c.at = Date.now(); }
  },

  /** 某房间的在线列表：15 秒内有动静，或者有长连接挂着 */
  list(room) {
    const now = Date.now();
    const out = [];
    for (const c of clients.values()) {
      if (c.room !== room) continue;
      const fresh = now - c.at < config.memberTtlMs;
      if (!fresh && !c.connected) continue;
      out.push({
        clientId: c.clientId, nickname: c.nickname, ip: c.ip,
        transport: c.transport, at: c.at,
        stale: !c.connected,     // 没有长连接的（轮询客户端）标黄
      });
    }
    return out.sort((a, b) => a.at - b.at);
  },
};
