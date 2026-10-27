/**
 * 任务三 · WebSocket
 *
 * 上下行都走同一条连接（握手是 HTTP，握手之后是独立协议）。
 * 断线自动重连：指数退避 1s → 2s → 4s → 8s，上限 10s；
 * 重连成功后自动重新加入原房间，并带上本地最大 seq，让服务端补齐断线期间的消息。
 *
 * ⚠️ 三条"不能崩"的纪律（都是踩过的坑）：
 *  1. **每条请求带唯一 id**，回执按 id 匹配 —— 否则同时发两条时，第一条的回执会把两条都解决掉；
 *  2. **所有监听器/定时器都抓住当时的那个 socket 实例**（`const sock = ws`），
 *      绝不用外层可变的 `ws` —— 切传输后 `ws` 会被置成 null，定时器里再碰它就是
 *      "Cannot read properties of null"，一个未捕获异常直接冒到控制台；
 *  3. **连接一断，立刻把所有在途请求 reject 掉** —— 让调用方马上拿到明确错误，
 *      而不是挂在那儿等 4 秒超时（"走不了就明确拒绝"）。
 */
import { clientId, nickname } from '../session.js';

const SEND_TIMEOUT_MS = 4000;

export function createWsTransport({ room, since, onMessages, onMembers, onStatus }) {
  let ws = null;
  let alive = false;
  let attempt = 0;
  let retryTimer = null;
  let reqSeq = 0;
  const pending = new Map();      // id -> { resolve, reject, timer, sock, onMessage }
  const stats = { received: 0, sent: 0, reconnects: 0, rtt: 0 };

  function endpoint() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${location.host}/ws/${encodeURIComponent(room)}`;
  }

  /** 统一收尾：清定时器、摘监听器、从表里删掉，然后才 resolve/reject */
  function settle(entry, id, done) {
    clearTimeout(entry.timer);
    try { entry.sock.removeEventListener('message', entry.onMessage); } catch { /* socket 可能已关 */ }
    pending.delete(id);
    done();
  }

  /** 连接断了 / 主动停了：把在途请求全部明确拒绝，别让调用方干等 */
  function rejectAllPending(reason) {
    for (const [id, entry] of [...pending]) {
      settle(entry, id, () => entry.reject(new Error(reason)));
    }
  }

  function connect() {
    if (!alive) return;
    if (attempt === 0) onStatus?.('connecting', '正在握手…');
    else onStatus?.('reconnecting', `第 ${attempt} 次重连 · ${Math.min(1000 * 2 ** (attempt - 1), 10000)}ms 后`);

    const sock = new WebSocket(endpoint());      // ← 本轮的 socket 实例，下面的回调都只用它
    ws = sock;

    sock.onopen = () => {
      if (sock !== ws) return;                   // 已经换了新连接，这个过期了
      const retried = attempt > 0;
      attempt = 0;
      if (retried) stats.reconnects++;
      onStatus?.('online', retried ? '已重连 · 自动重新加入房间' : 'WebSocket 已连接');
      try {
        sock.send(JSON.stringify({
          type: 'hello',
          room,
          since: since(),
          clientId: clientId(),
          nickname: nickname(),
        }));
      } catch (err) {
        onStatus?.('error', '握手消息发送失败：' + (err?.message ?? err));
      }
    };

    sock.onmessage = (ev) => {
      if (sock !== ws) return;
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }

      if (msg.type === 'welcome' || msg.type === 'messages') {
        if (msg.members) onMembers?.(msg.members);
        const batch = msg.messages ?? [];
        stats.received += batch.length;
        onMessages?.(batch);
      } else if (msg.type === 'members') {
        onMembers?.(msg.members ?? []);
      } else if (msg.type === 'broadcast') {
        const batch = msg.messages ?? [];
        stats.received += batch.length;
        onMessages?.(batch);
      } else if (msg.type === 'ack') {
        stats.sent++;
        if (typeof msg.rtt === 'number') stats.rtt = msg.rtt;
      } else if (msg.type === 'error') {
        // 属于某条请求的错误由 send() 里的 onMessage 处理；这里只管连接级的
        if (!msg.id) onStatus?.('error', '服务端拒绝：' + msg.message);
      }
    };

    sock.onclose = (ev) => {
      if (sock !== ws) return;
      rejectAllPending('连接已断开，消息未发出');
      if (!alive) return;                        // 主动 stop() 不触发重连
      onStatus?.('reconnecting', `连接断开（code ${ev.code}）· 准备重连…`);
      const delay = Math.min(1000 * 2 ** attempt, 10000);
      attempt++;
      retryTimer = setTimeout(connect, delay);
    };

    sock.onerror = () => { /* 出错后 onclose 一定会来，重连逻辑统一放在 onclose */ };
  }

  return {
    name: 'ws',
    label: 'WebSocket',
    path: `ws://…/ws/${room}`,
    hint: '一条连接双向收发；断线指数退避重连，重连后自动重新加入房间并拉最新内容。',
    stats,

    start() {
      alive = true;
      connect();
    },

    /** WS 是唯一"上行也走长连接"的方案 */
    send(payload) {
      const sock = ws;
      // ① 走不了就**明确拒绝**，不做任何"假装成功"的事
      if (!sock || sock.readyState !== WebSocket.OPEN) {
        return Promise.reject(new Error('WebSocket 未连接，消息未发出（等它重连，或先切到轮询/SSE）'));
      }

      const id = `r${++reqSeq}`;
      return new Promise((resolve, reject) => {
        const entry = {
          sock,
          resolve,
          reject,
          timer: setTimeout(() => {
            settle(entry, id, () => reject(new Error('等待服务端确认超时（4 秒）')));
          }, SEND_TIMEOUT_MS),
          onMessage: (ev) => {
            let msg;
            try { msg = JSON.parse(ev.data); } catch { return; }
            if (msg.id !== id) return;            // ② 不是这一条的回执，别管
            if (msg.type === 'ack') {
              settle(entry, id, () => resolve(msg));
            } else if (msg.type === 'error') {
              settle(entry, id, () => reject(new Error(msg.message || '服务端拒绝')));
            }
          },
        };
        pending.set(id, entry);
        sock.addEventListener('message', entry.onMessage);

        try {
          sock.send(JSON.stringify({ type: 'send', id, payload }));
        } catch (err) {
          // send() 在某些状态下会同步抛（例如刚好在这一瞬断开）
          settle(entry, id, () => reject(new Error('发送失败：' + (err?.message ?? err))));
        }
      });
    },

    stop() {
      alive = false;
      if (retryTimer) clearTimeout(retryTimer);
      retryTimer = null;
      attempt = 0;
      rejectAllPending('已停止连接，消息未发出');
      const sock = ws;
      ws = null;
      try { sock?.close(1000, 'switch'); } catch { /* 已关闭 */ }
    },
  };
}
