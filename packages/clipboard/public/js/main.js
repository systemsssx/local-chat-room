/**
 * app.js —— 总装
 *
 * 这里只有三件事：
 *   ① 换管道（setTransport）：停旧 → 建新 → 用 store.lastSeq 接上。**绝不清空窗口**。
 *   ② 换输入源（setMode）：只替换底栏 composer。**不碰 store、不碰传输**。
 *   ③ 发消息（send）：所有模式、所有传输都走这一条路，返回值统一塞回 store（按 seq 去重）。
 *
 * 因为窗口只订阅 store，六个组合（2 模式 × 3 传输）在窗口上表现得完全一样，
 * 历史记录也是同一份 store —— 这就是"六个板块互通"的全部秘密。
 */
import { store } from './store.js';
import { api } from './api.js';
import { TRANSPORTS, transportMeta } from './transports/index.js';
import { createStreamRenderer } from './render.js';
import { createTextMode } from './modes/text.js';
import { createClipboardMode } from './modes/clipboard.js';
import { el, toast, fmtTime } from './ui.js';
import { clientId, nickname, setNickname, room, setRoom, isValidRoom, shortIp } from './session.js';

/* ---------- DOM ---------- */
const $ = (id) => document.getElementById(id);

/* ---------- 状态 ---------- */
const state = {
  room: room(),
  transportId: 'sse',
  modeId: 'text',
  transport: null,
  modes: {},
  mode: null,
  sessionStart: Date.now(),
  recentSeqs: [],       // 最近收到的 seq + 时间，用来算电平
  levels: { polling: 2, sse: 4, ws: 3 },
  net: null,            // 局域网地址信息（/api/net）
  db: null,             // 数据库实况（/api/health）
};

const renderer = createStreamRenderer({
  listEl: $('streamList'),      // 消息塞进这里（它自己不滚动，只是长高）
  scrollEl: $('msgs'),          // 真正带 overflow-y:auto 的容器 —— 自动滚动要作用在它身上
  store,
  onEmptyChange: (has) => $('streamEmpty')?.classList.toggle('hide', has),
});

/* ---------- 模式 ---------- */
state.modes = {
  text: createTextMode({
    mount: $('composerMount'),
    send, upload,
    room: state.room,
  }),
  clipboard: createClipboardMode({
    mount: $('composerMount'),
    send, upload,
  }),
};

/* ---------- 发消息：所有组合的唯一出口 ---------- */
async function send(payload) {
  if (!state.transport) throw new Error('尚未连接');
  const res = await state.transport.send({ ...payload, room: state.room });
  // 服务端返回的消息立即入 store；随后它还会从下行再到达一次 —— 按 seq 去重，窗口只显示一份
  if (res?.message) {
    store.apply([res.message]);
    // 自己发的消息一定让你看到：即便你当时正翻在上面的历史里，也直接滑到底部。
    // （别人发的消息则遵循"在底部才跟随"的规则，见 render.js）
    renderer.scrollToBottom();
  } else if (res?.ok) refreshSoon();
  return res;
}

async function upload(file) {
  return api.upload(state.room, file);
}

let refreshTimer = null;
function refreshSoon() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(async () => {
    try {
      const snap = await api.snapshot(state.room, store.lastSeq, state.transportId);
      store.apply(snap.messages ?? []);
      store.setMembers(snap.members ?? []);
    } catch { /* 下一轮会补上 */ }
  }, 120);
}

/* ---------- 换管道 ---------- */
async function setTransport(id) {
  if (state.transport && state.transport.name === id) return;
  state.transport?.stop();

  const meta = transportMeta(id);
  state.transportId = id;

  const transport = meta.create({
    room: state.room,
    since: () => store.lastSeq,               // ← 关键：新管道从"本地已有的最大 seq"接着要
    onMessages: (msgs) => {
      const added = store.apply(msgs);
      if (added) {
        const now = Date.now();
        for (let i = 0; i < added; i++) state.recentSeqs.push(now);
      }
      return added;
    },
    onMembers: (list) => store.setMembers(list),
    onStatus: (name, text, extra) => {
      store.setStatus(name, text, extra);
      paintStatus();
    },
  });

  state.transport = transport;
  paintChannels();
  paintTransportInfo(transport);
  paintCombo();
  await transport.start();
  store.setStatus('connecting', `已切到 ${meta.label}，正在接上…`);
  paintStatus();
}

/* ---------- 换输入源 ---------- */
function setMode(id) {
  if (state.mode?.id === id) return;
  state.mode?.detach();
  state.modeId = id;
  state.mode = state.modes[id];
  state.mode.attach();
  for (const btn of $('modeSeg').children) btn.classList.toggle('active', btn.dataset.mode === id);
  paintCombo();
}

/* ---------- 画面刷新 ---------- */
function paintStatus() {
  const s = store.status;
  const map = { online: '', connecting: ' w', reconnecting: ' w', error: ' r', idle: ' w' };
  const cls = 'led' + (map[s.name] ?? '');
  $('streamLed').className = cls;
  $('streamStatus').textContent = s.text ?? '';
  $('meLed').className = s.name === 'online' ? 'led' : 'led w';
  $('leftLed').className = cls;
  $('leftStatus').textContent = s.text ?? '';
}

function paintChannels() {
  renderChannelsInto($('channels'), state.transportId);
}

let renderChannelsInto = () => {};
function paintCombo() {
  const t = transportMeta(state.transportId);
  $('comboA').textContent = `CH${TRANSPORTS.findIndex((x) => x.id === state.transportId) + 1} · ${t.label}`;
  $('comboB').textContent = state.mode?.label ?? '';
}

function paintTransportInfo(t) {
  $('transportHint').textContent = t.hint ?? '';
  $('transportPath').textContent = t.path ?? '';
}

function paintMembers() {
  renderMembersInto($('members'), store.members);
  $('memberCount').textContent = String(store.members.length);
  $('leftOnline').textContent = `${store.members.length} 人在线`;
}
let renderMembersInto = () => {};

function paintService() {
  const secs = Math.floor((Date.now() - state.sessionStart) / 1000);
  // 服务地址显示的是"局域网地址"，而不是当前浏览器用的地址 ——
  // 这样在电脑上用 localhost 打开时，也能一眼看到手机该输什么
  const lanAddr = state.net?.primaryUrl?.replace(/^https?:\/\//, '') ?? null;
  const fallback = `${location.hostname}:${location.port || 80}`;
  renderServiceInto($('serviceInfo'), {
    room: state.room,
    total: store.count,
    addr: lanAddr ?? fallback,
    uptime: secs < 60 ? `${secs} 秒` : `${Math.floor(secs / 60)} 分 ${secs % 60} 秒`,
    db: state.db ? `${state.db.file} · ${state.db.sizeKB} KB` : 'data/app.db',
  });
  $('leftAddr').textContent = lanAddr ?? fallback;
}
let renderServiceInto = () => {};

async function paintHistory() {
  try {
    const res = await api.history(state.room, 3);
    renderHistoryInto($('historyList'), res.items ?? [], restore);
  } catch {
    renderHistoryInto($('historyList'), [], restore);
  }
}
let renderHistoryInto = () => {};

/** 新消息到达后刷新历史面板，但别每条都打一次接口 */
let historyTimer = null;
function scheduleHistory() {
  if (historyTimer) return;
  historyTimer = setTimeout(() => {
    historyTimer = null;
    paintHistory();
  }, 900);
}

function paintVu() {
  const now = Date.now();
  state.recentSeqs = state.recentSeqs.filter((t) => now - t < 3000);
  renderVuInto($('vu'), $('vuNum'), state.recentSeqs.length / 3);
  // 当前管道的电平梯随流量动
  state.levels[state.transportId] = Math.max(1, Math.min(5, 1 + state.recentSeqs.length));
  paintChannels();
}
let renderVuInto = () => {};

/* ---------- 历史恢复 ---------- */
async function restore(item) {
  try {
    await send({ type: 'text', text: item.text, source: 'history-restore' });
    toast('已恢复并广播给房间内所有人', 'info');
  } catch (err) {
    toast('恢复失败：' + (err?.message ?? err), 'error');
  }
}

/* ---------- 顶栏 / 左栏交互 ---------- */
function bindTop() {
  for (const btn of $('modeSeg').children) {
    btn.addEventListener('click', () => setMode(btn.dataset.mode));
  }
  $('roomBtn').addEventListener('click', applyRoom);
  $('roomInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') applyRoom(); });
  $('nickBtn').addEventListener('click', applyNick);
  $('nickInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') applyNick(); });
  $('nickInput').value = nickname();
  $('nickChip').textContent = nickname();
  $('avatar').textContent = nickname().slice(0, 1);
  $('roomInput').value = state.room;
  $('roomChipValue').textContent = state.room;
  $('refreshHistory').addEventListener('click', paintHistory);
}

async function applyRoom() {
  const v = $('roomInput').value.trim();
  if (!isValidRoom(v)) { toast('房间号只能用字母、数字、下划线、短横，1~32 位', 'error'); return; }
  if (v === state.room) return;
  setRoom(v);
  state.room = v;
  $('roomChipValue').textContent = v;
  // 换房间是**唯一**需要清空窗口的情况
  state.transport?.stop();
  state.transport = null;
  store.reset(v);
  renderer.reset();
  await setTransport(state.transportId);
  await paintHistory();
  toast('已进入房间 ' + v, 'info');
}

function applyNick() {
  const v = setNickname($('nickInput').value);
  $('nickChip').textContent = v;
  $('nickInput').value = v;
  toast('昵称已改为 ' + v, 'info');
}

/* ---------- 启动 ---------- */
async function boot() {
  // 由 app.js 注入渲染函数，避免 panels.js 与 DOM 互相依赖
  const panels = await import('./panels.js');
  renderChannelsInto = (container) => panels.renderChannels(container, {
    transports: TRANSPORTS,
    activeId: state.transportId,
    levels: state.levels,
    onPick: (id) => setTransport(id),
  });
  renderMembersInto = (container, members) => panels.renderMembers(container, members, clientId());
  renderServiceInto = (container, info) => panels.renderService(container, info);
  renderHistoryInto = (container, items, onRestore) => panels.renderHistory(container, items, onRestore);
  renderVuInto = (container, numEl, rate) => panels.renderVu(container, numEl, rate);

  // 顶栏电平条：16 段骨架
  $('vu').replaceChildren(...Array.from({ length: 16 }, () => el('i')));

  bindTop();
  store.setRoom(state.room);
  setMode('text');
  paintChannels();
  paintCombo();
  paintStatus();
  paintService();

  // 取局域网地址：右栏"服务地址"要显示的是"手机该输什么"，而不是当前浏览器用的地址
  try {
    state.net = await fetch('/api/net').then((r) => r.json());
  } catch {
    state.net = null;
  }
  paintService();
  if (state.net?.primaryUrl && ['localhost', '127.0.0.1'].includes(location.hostname)) {
    toast(`手机访问 ${state.net.primaryUrl}（连同一个 WiFi）`, 'info');
  }

  // 数据库实况（右栏"数据库"那一行显示真实的文件与大小）
  const loadDbInfo = async () => {
    try { state.db = (await fetch('/api/health').then((r) => r.json())).db; } catch { /* 离线时忽略 */ }
    paintService();
  };
  await loadDbInfo();

  // store 是唯一数据源：窗口、计数、剪贴板回写，全部由它驱动
  let notifiedSeq = 0;
  store.subscribe((ev) => {
    if (ev.type === 'messages' || ev.type === 'reset') {
      const all = store.list();
      renderer.append(all);
      $('streamCount').textContent = `${store.count} 条`;
      // 任务一要求 6：页面显示最近一次更新的时间
      const last = all[all.length - 1];
      $('streamUpdated').textContent = last ? `最近更新 ${fmtTime(last.at)}` : '最近更新 —';
      for (const m of all) {
        if (m.seq <= notifiedSeq) continue;
        notifiedSeq = m.seq;
        // 只在**剪贴板模式激活时**才把远端内容写回本机剪贴板。
        // 否则你在纯文字模式下聊着天，别人的消息会悄悄覆盖你刚复制的东西。
        if (m.clientId !== clientId() && state.modeId === 'clipboard') {
          state.modes.clipboard.onRemoteMessage(m);
        }
      }
      scheduleHistory();
      paintService();
    }
    if (ev.type === 'members') paintMembers();
    if (ev.type === 'status') paintStatus();
    if (ev.type === 'room') $('roomChipValue').textContent = store.room;
  });

  await setTransport(state.transportId);
  await paintHistory();

  setInterval(paintVu, 1000);
  setInterval(paintService, 5000);
  setInterval(loadDbInfo, 30000);

  // 首次拉取（切换传输时也会自动补差额）
  try {
    const snap = await api.snapshot(state.room, 0, state.transportId);
    store.apply(snap.messages ?? []);
    store.setMembers(snap.members ?? []);
  } catch (err) {
    toast('连接服务失败：' + (err?.message ?? err), 'error');
  }

  // 允许用 #room=xxxx 直接进房间（分享链接、手动改地址栏都能用）
  window.addEventListener('hashchange', () => {
    const m = location.hash.match(/room=([A-Za-z0-9_-]{1,32})/);
    if (!m || m[1] === state.room) return;
    $('roomInput').value = m[1];
    applyRoom();
  });
}

window.__toast = toast;
boot();
