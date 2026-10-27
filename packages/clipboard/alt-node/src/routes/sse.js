/**
 * src/routes/sse.js —— 任务二的下行通道（Server-Sent Events）
 *
 * 一次 GET，响应头是 text/event-stream，服务器**故意不结束这个响应**，于是能一直往里写。
 * 这条连接上只有"服务器 → 浏览器"这一个方向；浏览器要发东西仍然走 POST /api/messages。
 */
import { config } from '../config.js';
import { roomStore, hub, presence } from '../rooms.js';
import { validRoom } from './api.js';

const frame = (event, data) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

export function mountSseRoute(app) {
  app.get('/api/sse/:room', (req, res) => {
    const { room } = req.params;
    if (!validRoom(room)) return res.status(400).end();

    const client = presence.touch(req, room, {
      clientId: req.query.clientId,
      nickname: req.query.nickname,
      transport: 'sse',
    });
    presence.setConnected(client.clientId, true);

    res.writeHead(200, {
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
      'X-Accel-Buffering': 'no',      // 让 nginx 之类的反代不要缓冲
    });

    res.write(frame('hello', { seq: roomStore.seq, clientId: client.clientId }));

    // 连上先补差额（题目要求④：新加入的客户端能获得当前内容）
    const since = Number(req.query.since) || 0;
    const missed = roomStore.since(room, since);
    if (missed.length) res.write(frame('messages', { messages: missed }));

    hub.addSse(room, res);
    hub.broadcastMembers(room, presence.list(room));
    console.log(`[sse] ${client.nickname} 进入 ${room} · 补齐 ${missed.length} 条 · 订阅者 ${hub.sseCount(room)}`);

    // 心跳：长连接太久没数据会被中间的代理掐掉
    const ping = setInterval(() => {
      try { res.write(': ping\n\n'); } catch { /* ignore */ }
    }, config.ssePingMs);

    // 题目要求⑤：客户端断开后服务端要清理对应连接
    req.on('close', () => {
      clearInterval(ping);
      hub.removeSse(room, res);
      presence.setConnected(client.clientId, false);
      hub.broadcastMembers(room, presence.list(room));
      console.log(`[sse] ${client.nickname} 离开 ${room} · 订阅者 ${hub.sseCount(room)}`);
    });
  });
}
