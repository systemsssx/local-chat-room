/** rooms.js —— 左栏房间列表 + 建房间/加入房间弹窗 + 右栏成员与房间概况。
 *
 * 「建房间」和「加入房间」都走同一个弹窗组件，**样式和登录卡片是同一套**
 * （`.login-card` 那组类），所以三处输入框看起来是一家的。
 */

import { api } from './api.js';
import { getRoom, patchRoom, state } from './state.js';
import { $, clear, displayText, el, fmtShort, initial, toast } from './ui.js';

let hooks = {};
let visibility = 'private';

export function initRooms(options) {
  hooks = options ?? {};

  $('roomFilter').addEventListener('input', renderRoomList);
  $('newRoomBtn').addEventListener('click', () => openModal('create'));
  $('joinRoomBtn').addEventListener('click', () => openModal('join'));
  $('modalClose').addEventListener('click', closeModal);
  $('modal').addEventListener('click', (event) => {
    if (event.target === $('modal')) closeModal();      // 点蒙层关掉
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !$('modal').hidden) closeModal();
  });

  $('visSeg').addEventListener('click', (event) => {
    const btn = event.target.closest('.seg2-btn');
    if (!btn) return;
    visibility = btn.dataset.v;
    for (const node of $('visSeg').querySelectorAll('.seg2-btn')) {
      node.classList.toggle('active', node === btn);
    }
    $('visHint').textContent = visibility === 'public'
      ? '所有人能在公开列表里看到，加入仍需要房间码'
      : '只有拿到房间码的人才能加入';
  });

  $('createForm').addEventListener('submit', onCreate);
  $('joinForm').addEventListener('submit', onJoin);

  $('copyCode').addEventListener('click', copyCode);
  $('resetCode').addEventListener('click', resetCode);
}

/* ── 弹窗 ───────────────────────────────────────────── */

export function openModal(mode) {
  $('modalError').hidden = true;
  const isCreate = mode === 'create';
  $('modal').hidden = false;
  $('createForm').hidden = !isCreate;
  $('joinForm').hidden = isCreate;
  $('modalTitle').textContent = isCreate ? '新建房间' : '加入房间';
  $('modalTag').textContent = isCreate ? 'CREATE ROOM' : 'JOIN BY CODE';
  setTimeout(() => (isCreate ? $('roomNameInput') : $('joinCodeInput')).focus(), 30);
}

export function closeModal() {
  $('modal').hidden = true;
  $('createForm').reset();
  $('joinForm').reset();
  $('modalError').hidden = true;
}

function modalError(message) {
  const box = $('modalError');
  box.textContent = displayText(message, '出了点问题，详情看控制台');
  box.hidden = false;
}

async function onCreate(event) {
  event.preventDefault();
  const submit = $('createForm').querySelector('button[type=submit]');
  const name = $('roomNameInput').value.trim();
  if (!name) return modalError('给房间起个名字吧');
  submit.disabled = true;
  try {
    const { room } = await api.createRoom(name, visibility);
    closeModal();
    toast(`房间已创建 · 房间码 ${room.code}`);
    hooks.onCreated?.(room);
  } catch (err) {
    modalError(err.message);
  } finally {
    submit.disabled = false;
  }
}

async function onJoin(event) {
  event.preventDefault();
  const submit = $('joinForm').querySelector('button[type=submit]');
  const code = $('joinCodeInput').value.trim().toUpperCase();
  if (code.length !== 6) return modalError('房间码是 6 位');
  submit.disabled = true;
  try {
    const result = await api.joinRoom(code);
    closeModal();
    toast(result.joined ? `已加入《${result.room.name}》` : `你已经在《${result.room.name}》里了`);
    hooks.onJoined?.(result.room);
  } catch (err) {
    modalError(err.message);
  } finally {
    submit.disabled = false;
  }
}

/* ── 左栏房间列表 ────────────────────────────────────── */

export function renderRoomList() {
  const keyword = $('roomFilter').value.trim().toLowerCase();
  const list = state.rooms.filter((room) => !keyword || room.name.toLowerCase().includes(keyword));
  const box = $('roomList');
  clear(box);
  $('roomCount').textContent = String(state.rooms.length);

  if (!state.rooms.length) {
    box.appendChild(el('div', { class: 'rooms-empty' }, [
      el('div', { text: '还没有房间。' }),
      el('div', {}, [
        el('b', { text: '＋' }), document.createTextNode(' 新建一个，或者用 '),
        el('b', { text: '⌨' }), document.createTextNode(' 输入别人给的房间码。'),
      ]),
    ]));
    return;
  }
  if (!list.length) {
    box.appendChild(el('div', { class: 'rooms-empty', text: `没有名字里带「${keyword}」的房间` }));
    return;
  }
  for (const room of list) box.appendChild(roomNode(room));
}

function roomNode(room) {
  const active = room.id === state.roomId;
  const node = el('div', {
    class: 'room' + (active ? ' active' : '') + (room.unread ? ' unread' : ''),
    title: `${room.name} · ${room.members} 人 · 房间码 ${room.code}`,
  }, [
    el('div', { class: 'rm-txt' }, [
      el('div', { class: 'rm-nm' }, [
        document.createTextNode(room.name),
        el('span', { class: 'rm-cnt', text: `${room.members} 人` }),
      ]),
      el('div', { class: 'rm-sub', text: room.lastText || '还没有消息' }),
    ]),
    el('div', { class: 'rm-side' }, [
      el('div', { class: 'rm-time', text: fmtShort(room.lastAt) }),
      room.unread ? el('div', { class: 'badge', text: room.unread > 99 ? '99+' : String(room.unread) }) : null,
    ]),
  ]);
  node.addEventListener('click', () => hooks.onSwitchRoom?.(room.id));
  return node;
}

/* ── 右栏：成员 ─────────────────────────────────────── */

export function renderMembers() {
  const box = $('members');
  clear(box);
  const members = state.members ?? [];
  $('onlineCount').textContent = String(members.filter((m) => m.online).length);

  if (!members.length) {
    box.appendChild(el('div', { class: 'kv', text: '这个房间暂时没有人' }));
    return;
  }
  for (const member of members) {
    const mine = member.userId === state.user?.id;
    box.appendChild(el('div', { class: 'mem' + (member.online ? ' online' : '') }, [
      el('span', { class: 'av', text: initial(member.username) }),
      el('div', { class: 'mem-txt' }, [
        el('div', { class: 'mem-nm', text: member.username }),
        el('div', { class: 'mem-st', text: member.online ? '在线' : '离线' }),
      ]),
      member.role === 'owner' ? el('span', { class: 'chip dim', text: '房主' })
        : (mine ? el('span', { class: 'chip', text: '我' }) : null),
    ]));
  }
}

/* ── 右栏：房间概况 + 房间码 ─────────────────────────── */

export function renderRoomInfo() {
  const room = getRoom(state.roomId);
  const box = $('roomInfo');
  clear(box);
  if (!room) {
    $('roomCode').textContent = '——————';
    return;
  }
  const rows = [
    ['房间', room.name],
    ['房间码', room.code],
    ['成员', `${room.members} 人`],
    ['消息', `${room.lastSeq} 条`],
    ['房主', room.ownerName],
    ['建群', new Date(room.createdAt).toLocaleDateString('zh-CN')],
  ];
  for (const [key, value] of rows) {
    box.appendChild(el('div', { class: 'kv2' }, [
      el('span', { text: key }), el('b', { text: displayText(value, '—') }),
    ]));
  }
  $('roomCode').textContent = displayText(room.code, '——————');
  const isOwner = room.ownerId === state.user?.id;
  $('resetCode').disabled = !isOwner;
  $('resetCode').title = isOwner ? '换一个新的房间码（旧码立刻失效）' : '只有房主能重置房间码';

  box.appendChild(el('div', { class: 'card-actions' }, [
    el('button', { class: 'mini', text: '退出房间', onclick: leaveCurrentRoom }),
  ]));
}

async function leaveCurrentRoom() {
  const room = getRoom(state.roomId);
  if (!room) return;
  if (!confirm(`退出《${room.name}》？房间码还在的话随时可以再进来。`)) return;
  try {
    await api.leaveRoom(room.id);
    toast(`已退出《${room.name}》`);
    hooks.onLeft?.(room.id);
  } catch (err) {
    toast(err.message, 'error');
  }
}

async function copyCode() {
  const room = getRoom(state.roomId);
  if (!room) return;
  const text = room.code;
  try {
    await navigator.clipboard.writeText(text);         // 只在 https / localhost 可用
    toast(`房间码 ${text} 已复制`);
  } catch {
    // 局域网 http 下没有剪贴板 API：退回到老办法（选中 + execCommand）
    const temp = el('textarea', { style: 'position:fixed;opacity:0' });
    temp.value = text;
    document.body.appendChild(temp);
    temp.select();
    const ok = document.execCommand?.('copy');
    temp.remove();
    toast(ok ? `房间码 ${text} 已复制` : `房间码是 ${text}，手动记一下吧`, ok ? '' : 'error');
  }
}

async function resetCode() {
  const room = getRoom(state.roomId);
  if (!room) return;
  if (!confirm('换一个新房间码？旧码会立刻失效，已经进群的人不受影响。')) return;
  try {
    const { code } = await api.resetCode(room.id);
    patchRoom(room.id, { code });
    renderRoomInfo();
    renderRoomList();
    toast(`新房间码：${code}`);
  } catch (err) {
    toast(err.message, 'error');
  }
}
