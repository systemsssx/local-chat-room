/**
 * session.js —— 会话身份：客户端 id / 昵称 / 房间号
 *
 * 三者都持久化在 localStorage（房间号同时写进 URL hash），
 * 刷新页面、切换传输方式都不会变 —— 这是"六个组合共用一个窗口"的前提之一：
 * 换个管道不代表换个人、换个房间。
 */
const LS_ID = 'cc.clientId';
const LS_NICK = 'cc.nickname';
const LS_ROOM = 'cc.room';

export function clientId() {
  let id = localStorage.getItem(LS_ID);
  if (!id) {
    id = (crypto.randomUUID?.() ?? String(Math.random()).slice(2)).replace(/-/g, '').slice(0, 10);
    localStorage.setItem(LS_ID, id);
  }
  return id;
}

export function nickname() {
  let n = localStorage.getItem(LS_NICK);
  if (!n) {
    n = '用户' + Math.floor(1000 + Math.random() * 9000);
    localStorage.setItem(LS_NICK, n);
  }
  return n;
}

export function setNickname(value) {
  const v = String(value ?? '').trim().slice(0, 16);
  if (v) localStorage.setItem(LS_NICK, v);
  return nickname();
}

/** 房间号只允许字母数字下划线短横，和服务端校验保持一致 */
export function isValidRoom(room) {
  return /^[A-Za-z0-9_-]{1,32}$/.test(String(room ?? ''));
}

export function room() {
  const m = location.hash.match(/room=([A-Za-z0-9_-]{1,32})/);
  if (m) return m[1];
  const saved = localStorage.getItem(LS_ROOM);
  return isValidRoom(saved) ? saved : '1001';
}

export function setRoom(value) {
  const v = String(value ?? '').trim();
  if (!isValidRoom(v)) return null;
  localStorage.setItem(LS_ROOM, v);
  location.hash = 'room=' + v;
  return v;
}

/** 本机 IP 短尾显示用：服务端会在消息里带 ip，这里只做展示裁剪 */
export function shortIp(ip) {
  if (!ip) return '';
  const parts = String(ip).split('.');
  return parts.length === 4 ? '.' + parts[3] : String(ip);
}
