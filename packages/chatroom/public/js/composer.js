/** composer.js —— 底部输入栏：文字 / 图片 / 文件。
 *
 * 图片和文件都走同一条路：先 POST /api/upload 拿回文件描述，
 * 再把它当作一条消息发出去 —— 二进制不进数据库，库里只存 URL 与元信息。
 * Ctrl+V 粘贴图片也走同一条路（浏览器不允许网页读剪贴板里的文件，但 paste 事件可以）。
 */

import { api } from './api.js';
import { $, toast } from './ui.js';

let roomId = null;
let busy = false;
let onSent = null;

export function initComposer(options = {}) {
  onSent = options.onSent ?? null;

  $('sendBtn').addEventListener('click', sendText);
  $('compInput').addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      sendText();
    }
  });

  $('pickImage').addEventListener('click', () => $('fileImage').click());
  $('pickFile').addEventListener('click', () => $('fileDoc').click());
  $('fileImage').addEventListener('change', (event) => {
    const file = event.target.files?.[0];
    if (file) uploadAndSend(file, 'image');
    event.target.value = '';
  });
  $('fileDoc').addEventListener('change', (event) => {
    const file = event.target.files?.[0];
    if (file) uploadAndSend(file, 'file');
    event.target.value = '';
  });

  $('compInput').addEventListener('paste', (event) => {
    const item = [...(event.clipboardData?.items ?? [])].find((i) => i.type.startsWith('image/'));
    if (!item) return;
    const file = item.getAsFile();
    if (file) {
      event.preventDefault();
      uploadAndSend(file, 'image');
    }
  });
}

export function setComposerRoom(id) {
  roomId = id;
  $('compInput').disabled = !id;
  $('sendBtn').disabled = !id;
}

export function setComposerPlaceholder(text) {
  $('compInput').placeholder = text;
}

export function focusComposer() {
  $('compInput').focus();
}

async function sendText() {
  const input = $('compInput');
  const text = input.value.trim();
  if (!text || !roomId || busy) return;
  busy = true;
  try {
    const { message } = await api.send(roomId, { type: 'text', text });
    input.value = '';
    onSent?.([message]);
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    busy = false;
  }
}

async function uploadAndSend(file, kind) {
  if (!roomId || busy) return;
  if (file.size > 2 * 1024 * 1024) return toast('文件超过 2MB', 'error');
  busy = true;
  setBusyText('上传中…');
  try {
    const { file: info } = await api.upload(roomId, file);
    const { message } = await api.send(roomId, { type: kind, text: '', file: info });
    onSent?.([message]);
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    busy = false;
    setBusyText(null);
  }
}

function setBusyText(text) {
  const button = $('sendBtn');
  if (text) {
    button.dataset.label = button.textContent;
    button.textContent = text;
  } else if (button.dataset.label) {
    button.textContent = button.dataset.label;
    delete button.dataset.label;
  }
}
