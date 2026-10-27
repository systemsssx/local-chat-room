/** main.js —— 装配层：把登录、房间列表、消息流、SSE 串起来。
 *
 * 启动顺序：
 *    问 /api/auth/me
 *      ├─ 401 → 显示登录页
 *      └─ 200 → 拉房间列表 → 选中一个房间 → 拉快照 → 连 SSE
 *
 * 房间切换 = 关掉旧 SSE、清空消息、拉新房间快照、按新房间重连。
 * 一条 SSE 连接只服务一个房间（同时挂着"我的房间列表"那个用户频道）。
 */

import { api, setUnauthorizedHandler } from './api.js';
import { initAuth, showApp, showLogin } from './auth.js';
import { initComposer, setComposerPlaceholder, setComposerRoom } from './composer.js';
import { StreamRenderer } from './render.js';
import { initRooms, renderMembers, renderRoomInfo, renderRoomList } from './rooms.js';
import { connect } from './sse.js';
import { getRoom, patchRoom, removeRoom, savedRoomId, saveRoomId, setRooms, setUser, state } from './state.js';
import { MessageStore } from './store.js';
import { $, displayText, fmtShort, initial, toast } from './ui.js';

const store = new MessageStore();
const vu = { stamps: [] };

let renderer = null;
let sse = null;
let roomId = null;
let netInfo = null;
let sessionExpiredShown = false;      // "登录过期"只提示一次，避免一串 401 刷屏

/* ══════════════ 启动 ══════════════ */

function boot() {
  renderer = new StreamRenderer({
    listEl: $('streamList'),
    scrollEl: $('msgs'),
    emptyEl: $('streamEmpty'),
    jumpEl: $('jumpWrap'),
    jumpBtn: $('jumpBtn'),
  });
  ensureVuBars();

  initAuth({ onSuccess: onAuthed });
  initRooms({
    onSwitchRoom: switchRoom,
    onCreated: async (room) => { await refreshRooms(); await switchRoom(room.id); },
    onJoined: async (room) => { await refreshRooms(); await switchRoom(room.id); },
    onLeft: async (leftId) => {
      removeRoom(leftId);
      if (roomId === leftId) {
        detachRoom();
        const fallback = state.rooms[0]?.id ?? null;
        if (fallback) await switchRoom(fallback);
        else showNoRoom();
      }
      renderRoomList();
    },
  });
  initComposer({ onSent: (messages) => absorb(messages, { mine: true }) });

  setUnauthorizedHandler(() => {
    // 401 常常一次来好几个（并发请求 + SSE 重连），提示只弹一次，不然会刷屏
    if (sessionExpiredShown) return;
    sessionExpiredShown = true;
    if (state.user) {
      setUser(null);
      teardownSession();
      showLogin('登录已过期，请重新登录');
    }
  });

  $('logoutBtn').addEventListener('click', logout);
  $('loadMore').addEventListener('click', loadMore);
  $('openRooms').addEventListener('click', () => document.body.classList.toggle('show-rooms'));
  $('openMembers').addEventListener('click', () => document.body.classList.toggle('show-members'));
  $('roomList').addEventListener('click', () => document.body.classList.remove('show-rooms'));

  setInterval(tickVu, 1000);
  checkSession();
}

async function checkSession() {
  try {
    const { user } = await api.me();
    await onAuthed(user);
  } catch (err) {
    showLogin(err.status === 401 ? '' : err.message);
  }
}

async function onAuthed(user) {
  setUser(user);
  sessionExpiredShown = false;        // 重新登录成功，恢复提示
  $('myName').textContent = displayText(user.username, '我');
  $('myAvatar').textContent = initial(user.username);
  showApp();

  await refreshRooms();
  loadNetInfo();

  const preferred = savedRoomId();
  const target = (preferred && getRoom(preferred)) ? preferred : (state.rooms[0]?.id ?? null);
  if (target) await switchRoom(target);
  else showNoRoom();
}

function teardownSession() {
  sse?.close();
  sse = null;
  roomId = null;
  state.roomId = null;
  state.rooms = [];
  state.members = [];
  store.clear();
  renderer?.reset();
  saveRoomId(null);
  renderRoomList();
  renderRoomInfo();
  renderMembers();
}

async function logout() {
  try { await api.logout(); } catch { /* 票已经没了也没关系 */ }
  teardownSession();
  setUser(null);
  showLogin();
}

/* ══════════════ 房间 ══════════════ */

async function refreshRooms() {
  const { rooms } = await api.rooms();
  setRooms(rooms);
  renderRoomList();
  renderRoomInfo();
}

async function switchRoom(nextId) {
  if (!nextId || nextId === roomId) return;
  document.body.classList.remove('show-rooms');

  sse?.close();
  sse = null;
  roomId = nextId;
  state.roomId = nextId;
  state.latestSeq = 0;
  saveRoomId(nextId);

  store.clear();
  renderer.reset();
  setComposerRoom(null);
  setComposerPlaceholder('正在载入房间…');
  renderRoomList();                       // 立刻高亮新房间
  updateHead();

  try {
    const data = await api.snapshot(nextId, 0);
    if (roomId !== nextId) return;      // 等结果时又切了房间：这份结果作废（否则会画错房间）
    state.members = data.members ?? [];
    state.latestSeq = data.latestSeq ?? 0;
    // 进房间 = 已读到最新：本地把未读清掉（服务端也在快照里做了同一件事）
    patchRoom(nextId, { unread: 0, lastSeq: data.latestSeq ?? 0 });
    const added = store.apply(data.messages ?? []);
    renderer.renderAll(added);
    $('loadMoreWrap').hidden = !data.hasMore;
    renderMembers();
    renderRoomInfo();
    renderRoomList();
    updateHead();
    setComposerRoom(nextId);
    setComposerPlaceholder(`发消息到 #${getRoom(nextId)?.name ?? ''}`);
    connectSse(nextId);
  } catch (err) {
    if (err.status === 403 || err.status === 404) {
      toast('这个房间不存在或你已经不在里面了', 'error');
      removeRoom(nextId);
      roomId = null;
      state.roomId = null;
      saveRoomId(null);
      renderRoomList();
      const fallback = state.rooms[0]?.id ?? null;
      if (fallback) await switchRoom(fallback);
      else showNoRoom();
      return;
    }
    // 其它错误（断网等）：把当前房间清掉，这样用户再点一次这个房间就能重试
    toast(`${err.message}（再点一下房间重试）`, 'error');
    roomId = null;
    state.roomId = null;
    setComposerRoom(null);
    setComposerPlaceholder('先新建一个房间，或用房间码加入一个');
    renderRoomList();
    updateHead();
  }
}

/** 卸下当前房间：断开连接、清空消息、清掉"上次进的房间" */
function detachRoom() {
  sse?.close();
  sse = null;
  roomId = null;
  state.roomId = null;
  saveRoomId(null);
  store.clear();
  renderer.reset();
}

function showNoRoom() {
  detachRoom();
  $('loadMoreWrap').hidden = true;
  setComposerRoom(null);
  setComposerPlaceholder('先新建一个房间，或用房间码加入一个');
  $('streamEmpty').textContent = '还没有进入任何房间 —— 点左栏的 ＋ 新建，或点 ⌨ 输入房间码';
  $('roomName').textContent = '# 还没有房间';
  $('roomMeta').textContent = '—';
  state.members = [];
  renderMembers();
  renderRoomInfo();
  renderRoomList();
  updateHead();
}

/* ══════════════ SSE ══════════════ */

let lastRecheck = 0;

/** 连不上时问一句"我还在这个房间里吗"。
 *
 * EventSource 的 error 事件不告诉我们状态码，而两种失败它都救不回来：
 * 票没了（401）和被移出房间（403）。401 走 onUnauthorized，这里管后者 ——
 * 身份还在却一直连不上，多半是房间没了，于是重拉房间列表、换个房间。
 */
async function recheckRoom() {
  const now = Date.now();
  if (now - lastRecheck < 5000) return;      // 节流：别让重连风暴打爆接口
  lastRecheck = now;
  try {
    await refreshRooms();
    if (roomId && !getRoom(roomId)) {
      toast('你已经不在这个房间里了', 'error');
      detachRoom();
      const fallback = state.rooms[0]?.id ?? null;
      if (fallback) await switchRoom(fallback);
      else showNoRoom();
    }
  } catch { /* 拉不到就等下一次 */ }
}

function connectSse(targetRoomId) {
  sse = connect({
    roomId: targetRoomId,
    since: store.lastSeq,
    handlers: {
      onHello: (data) => {
        state.members = data.members ?? [];
        renderMembers();
        setConnState('open');
      },
      onMessages: (messages) => absorb(messages),
      onMembers: (members) => {
        state.members = members;
        // 左栏那行「12 人」也归这个事件管 —— 否则成员栏变了、房间列表还是旧的
        patchRoom(roomId, { members: members.length });
        renderMembers();
        renderRoomInfo();
        renderRoomList();
        updateHead();
      },
      onRooms: (rooms) => {
        setRooms(rooms);
        renderRoomList();
        renderRoomInfo();
      },
      onRoomUpdated: onRoomUpdated,
      onRoomGone: (data) => {
        removeRoom(data.roomId);
        renderRoomList();
        if (data.roomId === roomId) {
          toast('这个房间已经解散了', 'error');
          roomId = null;
          state.roomId = null;
          const fallback = state.rooms[0]?.id ?? null;
          if (fallback) switchRoom(fallback); else showNoRoom();
        }
      },
      onStatus: setConnState,
      onRecheck: recheckRoom,
      onUnauthorized: () => {
        setUser(null);
        teardownSession();
        showLogin('登录已过期，请重新登录');
      },
    },
  });
}

/** 收到消息：入库去重 → 画出来 → 记速率。mine=true 表示是自己刚发的。 */
function absorb(messages, { mine = false } = {}) {
  if (!messages?.length) return;
  // 迟到的推送（刚切过房间时可能还有一两条在路上）不属于当前房间，丢掉
  const fresh = messages.filter((msg) => msg.roomId === roomId);
  if (!fresh.length) return;

  const added = store.apply(fresh);
  // 全都被去重了 → 直接返回。
  // 自己发的消息**总是走两条路到达**：POST 的响应 + SSE 推送，谁先到不一定。
  // 后到的那一次 added 是空数组，不加这个判断就会去读 added[-1].at 而抛异常
  // （表现是：消息显示正常，但每次发消息都弹一个红色报错）。
  if (!added.length) return;

  const last = added[added.length - 1];
  for (const msg of added) vu.stamps.push(Date.now());
  renderer.append(added);
  patchRoom(roomId, { lastSeq: store.lastSeq, lastAt: last.at, lastText: preview(last) });
  if (mine) patchRoom(roomId, { unread: 0 });
  renderRoomList();
  updateHead();
}

function preview(msg) {
  if (msg.type === 'image') return `${msg.username}：[图片]`;
  if (msg.type === 'file') return `${msg.username}：[文件]`;
  return `${msg.username}：${(msg.text || '').replace(/\n/g, ' ').slice(0, 60)}`;
}

/** 别的房间有新消息（后端推的轻量增量）：只更新列表和未读数 */
function onRoomUpdated(data) {
  const room = getRoom(data.roomId);
  if (!room) return;
  const patch = {};
  if (data.members !== undefined) patch.members = data.members;
  if (data.name) patch.name = data.name;
  if (data.code) patch.code = data.code;
  if (data.lastSeq !== undefined && data.lastSeq > (room.lastSeq ?? 0)) {
    patch.lastSeq = data.lastSeq;
    patch.lastAt = data.lastAt;
    patch.lastText = data.lastText ?? room.lastText;
    if (data.roomId !== roomId) {
      // 增量 = 这次多出来的条数（断线期间攒的几条也一次算清）
      patch.unread = (room.unread ?? 0) + Math.max(0, data.lastSeq - (room.lastSeq ?? data.lastSeq));
    }
  }
  patchRoom(data.roomId, patch);
  renderRoomList();
  if (data.roomId === roomId) renderRoomInfo();
}

/* ══════════════ 翻页 ══════════════ */

async function loadMore() {
  if (!roomId) return;
  const forRoom = roomId;               // 记下这一页是给哪个房间翻的
  const button = $('loadMore');
  const before = store.firstSeq || state.latestSeq + 1;
  button.disabled = true;
  button.textContent = '载入中…';
  try {
    const data = await api.history(forRoom, before, 30);
    if (roomId !== forRoom) return;     // 期间切了房间，这一页丢掉
    store.prepend(data.messages ?? []);
    renderer.prependAll(store.all());
    $('loadMoreWrap').hidden = !data.hasMore;
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    button.disabled = false;
    button.textContent = '加载更早的消息';
  }
}

/* ══════════════ 界面小件 ══════════════ */

function updateHead() {
  const room = getRoom(roomId);
  if (!room) return;
  $('roomName').textContent = displayText(`# ${room.name}`, '# 未命名房间');
  const online = (state.members ?? []).filter((m) => m.online).length;
  $('roomMeta').textContent = displayText(
    `${room.members} 位成员 · 在线 ${online} · 共 ${room.lastSeq} 条`, '—');
  $('msgCount').textContent = displayText(`${room.lastSeq} 条`, '0 条');
  $('msgUpdated').textContent = store.lastAt
    ? displayText(`最近更新 ${fmtShort(store.lastAt)}`, '—')
    : '还没有消息';
  $('streamEmpty').textContent = `《${room.name}》还没有消息 —— 在下面发第一条`;
  $('rightFoot').textContent = netInfo?.primaryUrl
    ? `手机连同一个 WiFi，打开 ${netInfo.primaryUrl}`
    : '同一个地址，手机也能打开';
}

function setConnState(status) {
  const map = {
    open: ['已连接', 'dim', 'SSE 推送中'],
    reconnecting: ['重连中…', 'warn', '连接中断，正在重连'],
    closed: ['已断开', 'err', '未连接'],
  };
  const [text, led, stream] = map[status] ?? map.open;
  $('connText').textContent = text;
  $('connLed').className = 'led' + (led === 'dim' ? '' : ' ' + led);
  $('streamState').textContent = stream;
  $('streamLed').className = 'led' + (led === 'dim' ? '' : ' ' + led);
  $('connFoot').textContent = text;
}

async function loadNetInfo() {
  try {
    netInfo = await api.net();
    const url = netInfo.primaryUrl ?? netInfo.localUrl ?? '';
    $('netAddr').textContent = url.replace(/^https?:\/\//, '');
    $('loginNote').textContent = `服务 ${url} · SSE 长连接\n密码只存哈希，服务端拿不到明文`;
    updateHead();
  } catch { /* 拿不到就算了，不影响聊天 */ }
}

/* —— 顶栏那个 msg/s 电平表（纯显示，不加任何逻辑）—— */

function ensureVuBars() {
  const box = $('vu');
  for (let i = 0; i < 5; i++) box.appendChild(document.createElement('i'));
}

function tickVu() {
  const now = Date.now();
  vu.stamps = vu.stamps.filter((t) => now - t < 5000);
  const rate = vu.stamps.length / 5;
  const level = Math.min(5, Math.ceil(rate));
  const bars = $('vu').children;
  for (let i = 0; i < bars.length; i++) bars[i].className = i < level ? 'on' : '';
  $('vuNum').textContent = `${rate.toFixed(1)} msg/s`;
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
