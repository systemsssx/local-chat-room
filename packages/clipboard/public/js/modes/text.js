/**
 * 输入模式一 · 纯文字
 *
 * 底栏＝「输入框 + 图片 + 文件 + 发送」。
 * 它只负责"把用户产出的东西变成一条待发送的消息"，然后交给统一的 send()。
 * —— 它不知道当前用的是轮询还是 WebSocket，也不碰信息窗口。
 */
import { el, toast } from '../ui.js';

export function createTextMode({ mount, send, upload, room }) {
  const input = el('input', {
    class: 'comp',
    placeholder: '输入要共享的内容…（Enter 发送）',
    autocomplete: 'off',
  });

  const imagePicker = el('input', {
    type: 'file', accept: 'image/png,image/jpeg,image/gif,image/webp', class: 'hidden-file',
  });
  const filePicker = el('input', {
    type: 'file',
    accept: '.txt,.md,.py,.js,.json,.css,.html,.csv,.log,.yml,.yaml,.ini,.conf,.sh',
    class: 'hidden-file',
  });

  const root = el('div', { class: 'composer' }, [
    input,
    el('button', { class: 'tool', onclick: () => imagePicker.click() }, [
      el('span', { class: 'ti', text: '🖼' }), el('span', { class: 'tl', text: '图片' }),
    ]),
    el('button', { class: 'tool', onclick: () => filePicker.click() }, [
      el('span', { class: 'ti', text: '📄' }), el('span', { class: 'tl', text: '文件' }),
    ]),
    el('div', { class: 'comp-hint' }, ['支持 png / jpg / gif / webp 与 txt、py、md 等纯文本文件，单文件 ≤ 2 MB']),
    el('button', { class: 'send', onclick: submitText }, ['发送']),
    imagePicker,
    filePicker,
  ]);

  async function submitText() {
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    try {
      await send({ type: 'text', text, source: 'text' });
    } catch (err) {
      // 发送被拒（例如 WS 断了）→ 明确提示，并把内容放回输入框，别让用户白打一遍
      input.value = text;
      toast(`发送失败：${err?.message ?? err}`, 'error');
    }
  }

  async function submitFiles(files, kind) {
    for (const file of [...files]) {
      try {
        const res = await upload(file);
        await send({
          type: res.file.kind === 'image' ? 'image' : 'file',
          file: res.file,
          text: res.file.name,
          source: kind,
        });
      } catch (err) {
        const msg = err?.message ?? String(err);
        toast(`上传失败：${msg}`, 'error');
      }
    }
  }

  imagePicker.addEventListener('change', () => submitFiles(imagePicker.files, 'text-image'));
  filePicker.addEventListener('change', () => submitFiles(filePicker.files, 'text-file'));
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submitText(); }
  });

  /* 在输入框里直接 Ctrl+V 粘贴图片 */
  input.addEventListener('paste', (e) => {
    const files = [...(e.clipboardData?.files ?? [])].filter((f) => f.type.startsWith('image/'));
    if (files.length) { e.preventDefault(); submitFiles(files, 'text-paste'); }
  });

  return {
    id: 'text',
    label: '纯文字',
    hint: '手动输入或粘贴内容，点发送共享到房间',
    attach() { mount.replaceChildren(root); input.focus(); },
    detach() { /* DOM 被 replaceChildren 换掉，无需额外清理 */ },
    /** 纯文字模式不需要外部喂剪贴板内容 */
    onRemoteMessage() {},
  };
}
