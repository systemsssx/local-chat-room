/**
 * 任务一 · HTTP 轮询
 *
 * 下行：每 1.5 秒 GET /api/snapshot/:room?since=<本地最大 seq>
 * 上行：POST /api/messages/:room
 *
 * 这一版最"笨"：不管内容变没变，到点就问一次服务器。
 * 代价是一堆空请求，好处是没有任何连接管理 —— 断线、重连这些概念在这里不存在。
 */
import { api } from '../api.js';

export const POLL_INTERVAL = 1500;

export function createPollingTransport({ room, since, onMessages, onMembers, onStatus }) {
  let timer = null;
  let alive = false;
  const stats = { requests: 0, lastMs: 0, changed: 0 };

  async function tick() {
    const t0 = performance.now();
    try {
      const snap = await api.snapshot(room, since(), 'polling');
      stats.requests++;
      stats.lastMs = Math.round(performance.now() - t0);
      onMembers?.(snap.members);
      const added = onMessages?.(snap.messages ?? []) ?? 0;
      if (added > 0) stats.changed++;
      onStatus?.('online', `轮询中 · ${POLL_INTERVAL / 1000}s 一次 · 第 ${stats.requests} 次`);
    } catch (err) {
      onStatus?.('error', '轮询失败：' + (err?.message ?? err));
    }
  }

  return {
    name: 'polling',
    label: 'HTTP 轮询',
    path: `GET /api/snapshot/${room}`,
    hint: '客户端每 1.5 秒问一次控制器，内容没变也照问 —— 代码最少，空请求最多。',
    stats,

    async start() {
      alive = true;
      onStatus?.('connecting', '正在发起第一次请求…');
      await tick();
      if (!alive) return;
      timer = setInterval(tick, POLL_INTERVAL);
    },

    /** 轮询版没有长连接，发消息就是一次普通 POST */
    send(payload) {
      return api.send(room, { ...payload, transport: 'polling' });
    },

    stop() {
      alive = false;
      if (timer) clearInterval(timer);
      timer = null;
    },
  };
}
