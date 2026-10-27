/**
 * 任务二 · SSE（Server-Sent Events）
 *
 * 下行：一条 EventSource 长连接，服务器一有变化就推
 * 上行：**仍然是 POST** —— SSE 是单向的，这一点必须由代码本身说清楚，
 *       不能让"换了个实时方案"的错觉掩盖住"发消息还是普通 HTTP"。
 *
 * 断线补齐：EventSource 会自己重连，但重连后不会补发断线期间的消息，
 *           所以每次 onopen 都用 REST 拉一次 since=本地最大 seq 的差额。
 */
import { api } from '../api.js';

export function createSseTransport({ room, since, onMessages, onMembers, onStatus }) {
  let es = null;
  let opened = 0;
  const stats = { events: 0, pings: 0, reconnects: 0 };

  /** 每次（重）连上都补一次差额，保证不丢消息 */
  async function catchUp() {
    try {
      const snap = await api.snapshot(room, since(), 'sse');
      onMembers?.(snap.members);
      onMessages?.(snap.messages ?? []);
    } catch {
      /* 补差额失败不致命，下一条推送或下次重连还会补 */
    }
  }

  return {
    name: 'sse',
    label: 'SSE 推送',
    path: `GET /api/sse/${room}`,
    hint: '一条长连接由服务器主动推；写入仍走普通 POST，所以只是"半个"双向。',
    stats,

    start() {
      onStatus?.('connecting', '正在建立 SSE 连接…');
      es = new EventSource(`/api/sse/${encodeURIComponent(room)}`);

      es.addEventListener('open', () => {
        opened++;
        if (opened > 1) stats.reconnects++;
        onStatus?.('online', 'SSE 已连接 · 服务器推送通道就绪');
        catchUp();
      });

      es.addEventListener('messages', (ev) => {
        stats.events++;
        try {
          onMessages?.(JSON.parse(ev.data).messages ?? []);
        } catch { /* 忽略坏帧 */ }
      });

      es.addEventListener('members', (ev) => {
        try {
          onMembers?.(JSON.parse(ev.data).members ?? []);
        } catch { /* 忽略坏帧 */ }
      });

      es.addEventListener('ping', () => {
        stats.pings++;
      });

      es.addEventListener('error', () => {
        // EventSource 自己会重连，这里只如实反映状态
        onStatus?.('reconnecting', 'SSE 连接中断 · EventSource 正在自动重连…');
      });
    },

    /** 关键：SSE 只解决了下行，上行依旧是普通 HTTP 请求 */
    send(payload) {
      return api.send(room, { ...payload, transport: 'sse' });
    },

    stop() {
      es?.close();
      es = null;
    },
  };
}
