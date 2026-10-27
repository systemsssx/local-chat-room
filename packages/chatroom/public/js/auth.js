/** auth.js —— 登录 / 注册卡片。
 *
 * 同一个卡片两种模式：登录 / 注册，靠改文案与接口切换。
 * 提交成功后把 user 交给上层（main.js），由它去拉房间列表、连 SSE。
 */

import { api } from './api.js';
import { $, displayText, toast } from './ui.js';

let mode = 'login';
let onAuthed = null;

const COPY = {
  login: {
    title: '进入聊天室',
    sub: '用户名就是聊天室里显示的名字。第一次来点下面的「注册」，之后用同一个账号登录即可。',
    submit: '登录',
    hint: '还没有账号？',
    link: '注册一个',
    note: '密码只存哈希，服务端拿不到明文',
  },
  register: {
    title: '注册一个账号',
    sub: '用户名会显示在每条消息旁边，密码至少 8 位。注册完直接进聊天室，不用再登录一次。',
    submit: '注册并进入',
    hint: '已经有账号了？',
    link: '去登录',
    note: '密码用 pbkdf2 加盐哈希后入库，明文不落盘',
  },
};

export function initAuth({ onSuccess }) {
  onAuthed = onSuccess;
  $('authToggle').addEventListener('click', toggleMode);
  $('authToggle').addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); toggleMode(); }
  });
  $('authForm').addEventListener('submit', submit);
  applyCopy();
}

function toggleMode() {
  mode = mode === 'login' ? 'register' : 'login';
  applyCopy();
  hideError();
  $('authPass').value = '';
  $('authUser').focus();
}

function applyCopy() {
  const text = COPY[mode];
  $('authTitle').textContent = text.title;
  $('authSub').textContent = text.sub;
  $('authSubmit').textContent = text.submit;
  $('authHint').textContent = text.hint;
  $('authToggle').textContent = text.link;
  $('loginNote').textContent = text.note;
  $('authPass').setAttribute('autocomplete', mode === 'login' ? 'current-password' : 'new-password');
  if (mode === 'register') {
    $('authPass').placeholder = '至少 8 位';
    $('authUser').placeholder = '2–16 个字符，中英文都行';
  } else {
    $('authPass').placeholder = '至少 8 位';
    $('authUser').placeholder = '你的用户名';
  }
}

function showError(message) {
  const box = $('authError');
  box.textContent = displayText(message, '出了点问题，详情看控制台');
  box.hidden = false;
}

function hideError() {
  $('authError').hidden = true;
}

async function submit(event) {
  event.preventDefault();
  const username = $('authUser').value.trim();
  const password = $('authPass').value;
  const button = $('authSubmit');

  hideError();
  if (!username || !password) return showError('用户名和密码都要填');
  if (mode === 'register' && password.length < 8) return showError('密码至少 8 位');

  button.disabled = true;
  button.textContent = mode === 'login' ? '登录中…' : '注册中…';
  try {
    const data = mode === 'login'
      ? await api.login(username, password)
      : await api.register(username, password);
    $('authPass').value = '';
    onAuthed?.(data.user);
  } catch (err) {
    showError(err.message);
  } finally {
    button.disabled = false;
    button.textContent = COPY[mode].submit;
  }
}

export function showLogin(message = '') {
  $('appView').hidden = true;
  $('loginView').hidden = false;
  if (String(message ?? '')) {          // 空字符串 / undefined 都不要弹红框
    showError(message);
    toast(message, 'error');
  }
  setTimeout(() => $('authUser').focus(), 30);
}

export function showApp() {
  $('loginView').hidden = true;
  $('appView').hidden = false;
}
