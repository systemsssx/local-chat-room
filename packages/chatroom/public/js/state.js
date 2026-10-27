/** state.js —— 界面的唯一数据源（谁 + 我的房间 + 当前房间）。
 *
 * 只放"读多写少"的状态；消息不放这里（消息有它自己的 MessageStore）。
 * 房间号记在 localStorage 里：刷新页面还能回到上次那个房间。
 */

export const state = {
  user: null,        // {id, username}
  rooms: [],         // 我加入的房间（后端 /api/rooms 的形状）
  roomId: null,      // 当前房间 id
  members: [],       // 当前房间成员
  latestSeq: 0,      // 当前房间最新序号
  hasMore: false,    // 还有更早的消息可翻
  online: 0,         // 当前房间在线人数
};

const LS_ROOM = 'chat.roomId';

export function saveRoomId(roomId) {
  try {
    if (roomId) localStorage.setItem(LS_ROOM, String(roomId));
    else localStorage.removeItem(LS_ROOM);
  } catch { /* 隐私模式下 localStorage 可能不可用，忽略 */ }
}

export function savedRoomId() {
  try {
    const value = Number(localStorage.getItem(LS_ROOM));
    return Number.isFinite(value) && value > 0 ? value : null;
  } catch {
    return null;
  }
}

export function setUser(user) {
  state.user = user;
}

export function setRooms(list) {
  state.rooms = Array.isArray(list) ? list : [];
}

export function getRoom(roomId) {
  return state.rooms.find((r) => r.id === roomId) ?? null;
}

export function currentRoom() {
  return getRoom(state.roomId);
}

/** 后端推来的增量：只改几个字段，不用整表重拉 */
export function patchRoom(roomId, patch) {
  const room = getRoom(roomId);
  if (room) Object.assign(room, patch);
}

export function removeRoom(roomId) {
  state.rooms = state.rooms.filter((r) => r.id !== roomId);
}
