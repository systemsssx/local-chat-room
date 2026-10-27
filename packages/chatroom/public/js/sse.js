/** sse.js —— 唯一的实时通道（EventSource）。
 *
 * 为什么是它：`EventSource` 会**自动带 Cookie**（浏览器行为，不用写代码），
 * 也会自动重连 —— 这两件事正好是本项目需要的。
 *
 * 一条连接同时收两类事件：
 *   · 房间频道：messages / members（当前房间的事）
 *   · 用户频道：rooms / room-updated / room-gone（我的房间列表的事）
 *
 * 断线重连时 URL 不变，所以服务端会按最初的 `since` 把差额重发一遍；
 * 前端靠 seq 去重，不会出现重复消息。
 */

import { api } from './api.js';

export function connect({ roomId, since = 0, handlers = {} }) {
  const params = new URLSearchParams();
  if (roomId) params.set('room', String(roomId));
  if (since) params.set('since', String(since));

  const source = new EventSource(`/api/sse?${params.toString()}`);
  let closed = false;
  let checking = false;

  const on = (name, fn) => source.addEventListener(name, (event) => {
    let data = {};
    try { data = JSON.parse(event.data); } catch { /* ping 之类没有内容 */ }
    fn(data);
  });

  on('hello', (d) => handlers.onHello?.(d));
  on('messages', (d) => handlers.onMessages?.(d.messages ?? []));
  on('members', (d) => handlers.onMembers?.(d.members ?? []));
  on('rooms', (d) => handlers.onRooms?.(d.rooms ?? []));
  on('room-updated', (d) => handlers.onRoomUpdated?.(d));
  on('room-gone', (d) => handlers.onRoomGone?.(d));
  on('ping', () => { /* 心跳，什么也不做 */ });

  source.addEventListener('open', () => { if (!closed) handlers.onStatus?.('open'); });

  source.addEventListener('error', async () => {
    if (closed) return;
    handlers.onStatus?.('reconnecting');
    // EventSource 自己会重连，但有两种情况它**永远不会**再连上：
    //   · 票没了（401）→ 得回登录页；
    //   · 被移出房间 / 房间没了（403/404）→ 得去重拉房间列表、换个房间。
    // 而 EventSource 的 error 事件不告诉我们状态码，所以主动查一次。
    if (checking) return;
    checking = true;
    try {
      await api.me();
      handlers.onRecheck?.();          // 身份还在 —— 那多半是房间的问题
    } catch (err) {
      if (err.status === 401) {
        closed = true;
        source.close();
        handlers.onUnauthorized?.();
      }
    } finally {
      checking = false;
    }
  });

  return {
    close() {
      closed = true;
      source.close();
    },
  };
}
