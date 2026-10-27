/**
 * src/ws.js —— 任务三：WebSocket 双向同步
 *
 * 与 SSE 的本质区别：握手（一次带 Upgrade 的 HTTP 请求）之后，这条 TCP 连接
 * **不再按 HTTP 说话**，改用 WebSocket 自己的帧协议 —— 两端地位对等，
 * 谁都能在任何时刻发一个帧。所以它是"双向"。
 *
 * 这里还多了一层 SSE 没有的东西：**心跳 + 半开连接清理**（ping/pong）。
 * 长连接可能被中间设备悄悄掐断而两端都不知道，靠 ping 定期探活。
 */
import { WebSocketServer } from 'ws';
import { config } from './config.js';
import { roomStore, hub, presence } from './rooms.js';

export function attachWebSocket(server) {
  const wss = new WebSocketServer({ noServer: true });

  /* 握手：只接受 /ws/<房间号>，其余一律拒绝 */
  server.on('upgrade', (req, socket, head) => {
    const url = new URL(req.url, 'http://placeholder');
    const m = url.pathname.match(/^\/ws\/([A-Za-z0-9_-]{1,32})$/);
    if (!m) {
      socket.destroy();
      return;
    }
    wss.handleUpgrade(req, socket, head, (ws) => {
      ws.room = m[1];
      ws.req = req;
      ws.isAlive = true;
      wss.emit('connection', ws, req);
    });
  });

  wss.on('connection', (ws) => {
    const room = ws.room;
    let me = null;

    ws.on('pong', () => { ws.isAlive = true; });

    ws.on('message', (raw) => {
      let msg;
      try { msg = JSON.parse(raw.toString()); } catch { return; }

      /* ① 加入房间（重连后也会再发一次，带上本地最大 seq 请求补齐） */
      if (msg.type === 'hello') {
        me = presence.touch(ws.req, room, {
          clientId: msg.clientId,
          nickname: msg.nickname,
          transport: 'ws',
        });
        presence.setConnected(me.clientId, true);
        ws.clientId = me.clientId;
        hub.addWs(room, ws);

        const since = Number(msg.since) || 0;
        const missed = roomStore.since(room, since);
        ws.send(JSON.stringify({
          type: 'welcome',
          seq: roomStore.seq,
          messages: missed,                    // 题目要求④：新客户端自动同步当前内容
          members: presence.list(room),
        }));
        hub.broadcastMembers(room, presence.list(room));
        console.log(`[ws]  ${me.nickname} 加入 ${room} · 补齐 ${missed.length} 条 · 连接数 ${hub.wsCount(room)}`);
        return;
      }

      /* ② 发消息：唯一走 socket 上行的路径 */
      if (msg.type === 'send') {
        if (!me) {
          ws.send(JSON.stringify({ type: 'error', message: '还没 hello，先加入房间' }));
          return;
        }
        me.at = Date.now();
        const created = roomStore.push(room, msg.payload ?? {}, me);
        // 先回执（发起者靠它拿到 seq 立即上屏），再广播给房间内所有人
        ws.send(JSON.stringify({ type: 'ack', message: created, seq: created.seq, rtt: 0 }));
        hub.broadcastMessages(room, [created]);
        hub.broadcastMembers(room, presence.list(room));
        return;
      }

      /* ③ 应用层心跳（与协议层 ping/pong 并存，前端也能主动探活） */
      if (msg.type === 'ping') {
        ws.send(JSON.stringify({ type: 'pong', t: Date.now() }));
      }
    });

    /* 断开清理：题目提示里明确要求"及时从对应房间的集合中移除" */
    ws.on('close', () => {
      hub.removeWs(room, ws);
      if (me) presence.setConnected(me.clientId, false);
      hub.broadcastMembers(room, presence.list(room));
      console.log(`[ws]  ${me?.nickname ?? '?'} 离开 ${room} · 连接数 ${hub.wsCount(room)}`);
    });
    ws.on('error', () => { /* 出错后 close 一定会来，清理统一放 close */ });
  });

  /* 协议层心跳：没回 pong 的判为半开连接，直接断掉，避免连接集合里堆死连接 */
  const timer = setInterval(() => {
    for (const ws of wss.clients) {
      if (ws.isAlive === false) { ws.terminate(); continue; }
      ws.isAlive = false;
      try { ws.ping(); } catch { /* ignore */ }
    }
  }, config.wsPingMs);
  timer.unref?.();
  wss.on('close', () => clearInterval(timer));

  return wss;
}
