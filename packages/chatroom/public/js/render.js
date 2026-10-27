/** render.js —— 把消息画成 DOM。
 *
 * 三件事：
 *   · 消息行：左边一栏是"谁 + 什么时候"，右边是内容（图片 / 文件 / 文字）；
 *   · 日期分隔：跨天时插一条「今天 / 昨天 / 10月6日」；
 *   · 滚动策略：人在底部就自动跟到底；翻上去看历史时不打断，只浮一个「N 条新消息 ↓」。
 */

import { state } from './state.js';
import { el, extLabel, fmtBytes, fmtDay, fmtTime, dayKey, initial } from './ui.js';

function metaLine(file) {
  const parts = [fmtBytes(file.size)];
  if (file.lines) parts.push(`${file.lines} 行`);
  if (file.encoding) parts.push(file.encoding);
  return parts.filter(Boolean).join(' · ');
}

/** 只认自己服务端上传目录里的地址。
 *
 * 服务端已经做过白名单校验（`file.url` 必须是 `/uploads/…` 且确实传到本房间），
 * 这里再挡一道：万一哪天服务端漏了，前端也不会把 `javascript:` 之类的地址塞进 href/src。
 */
function safeUrl(file) {
  const url = String(file?.url ?? '');
  return url.startsWith('/uploads/') ? url : '';
}

function bodyChildren(msg) {
  const kids = [];
  if (msg.text) kids.push(el('div', { class: 'm-text', text: msg.text }));

  const file = msg.file;
  const url = safeUrl(file);
  if (!file || !url) return kids;

  if (msg.type === 'image') {
    kids.push(el('a', { class: 'thumb', href: url, target: '_blank', rel: 'noreferrer' }, [
      el('img', { class: 'thumb-img', src: url, alt: file.name || '图片', loading: 'lazy' }),
      el('div', { class: 'thumb-cap', text: `${file.name || '图片'} · ${fmtBytes(file.size)} · 点击看原图` }),
    ]));
  } else {
    kids.push(el('div', { class: 'filecard' }, [
      el('span', { class: 'mf-ico', text: extLabel(file.name) }),
      el('div', { class: 'mf-txt' }, [
        el('div', { class: 'mf-name', text: file.name || '文件' }),
        el('div', { class: 'mf-meta', text: metaLine(file) }),
      ]),
      el('a', { class: 'mf-btn', href: url, download: file.name || 'file', text: '下载' }),
    ]));
    if (file.preview) {
      kids.push(el('pre', { class: 'code-block', text: file.preview }));
    }
  }
  return kids;
}

export function messageNode(msg, fresh = false) {
  const mine = state.user && msg.userId === state.user.id;
  return el('div', { class: 'msg' + (mine ? ' me' : '') + (fresh ? ' fresh' : '') }, [
    el('div', { class: 'm-rail' }, [
      el('span', { class: 'av', text: initial(msg.username) }),
      el('div', { class: 'm-who' }, [
        el('div', { class: 'm-name', text: mine ? `${msg.username} · 我` : msg.username }),
        el('div', { class: 'm-time', text: fmtTime(msg.at) }),
      ]),
    ]),
    el('div', { class: 'm-body' }, bodyChildren(msg)),
  ]);
}

export class StreamRenderer {
  constructor({ listEl, scrollEl, emptyEl, jumpEl, jumpBtn }) {
    this.listEl = listEl;
    this.scrollEl = scrollEl;
    this.emptyEl = emptyEl;
    this.jumpEl = jumpEl;
    this.jumpBtn = jumpBtn;
    this.pending = 0;
    this._dayKey = null;

    this.jumpBtn?.addEventListener('click', () => this.scrollToBottom(true));
    this.scrollEl?.addEventListener('scroll', () => {
      if (this.atBottom()) this._clearPending();
    });
  }

  atBottom() {
    const box = this.scrollEl;
    return box.scrollHeight - box.scrollTop - box.clientHeight < 90;
  }

  scrollToBottom(force = false) {
    if (!force && !this.atBottom()) return;
    requestAnimationFrame(() => {
      this.scrollEl.scrollTop = this.scrollEl.scrollHeight;
      this._clearPending();
    });
  }

  _clearPending() {
    this.pending = 0;
    if (this.jumpEl) this.jumpEl.hidden = true;
  }

  _empty(show) {
    if (this.emptyEl) this.emptyEl.hidden = !show;
  }

  reset() {
    this.listEl.replaceChildren();
    this._dayKey = null;
    this._clearPending();
    this._empty(true);
  }

  /** 全量重画（首屏、切房间、翻页后都用它） */
  renderAll(messages) {
    this.listEl.replaceChildren();
    this._dayKey = null;
    this._append(messages, false);
    this.scrollToBottom(true);
  }

  /** 追加新消息：在底部就跟着走，否则只记数、浮按钮 */
  append(messages, { fresh = true } = {}) {
    if (!messages?.length) return;
    const stick = this.atBottom();
    this._append(messages, fresh);
    if (stick) this.scrollToBottom(true);
    else {
      this.pending += messages.length;
      if (this.jumpBtn) this.jumpBtn.textContent = `${this.pending} 条新消息 ↓`;
      if (this.jumpEl) this.jumpEl.hidden = false;
    }
  }

  /** 往上翻页：保住当前的阅读位置（不然内容会"跳"一下） */
  prependAll(messages) {
    if (!messages?.length) return;
    const before = this.scrollEl.scrollHeight - this.scrollEl.scrollTop;
    this.listEl.replaceChildren();
    this._dayKey = null;
    this._append(messages, false);
    requestAnimationFrame(() => {
      this.scrollEl.scrollTop = this.scrollEl.scrollHeight - before;
    });
  }

  _append(messages, fresh) {
    const frag = document.createDocumentFragment();
    for (const msg of messages ?? []) {
      const key = dayKey(msg.at);
      if (key !== this._dayKey) {
        this._dayKey = key;
        frag.appendChild(el('div', { class: 'day' }, [el('span', { text: fmtDay(msg.at) })]));
      }
      frag.appendChild(messageNode(msg, fresh));
    }
    if (frag.childNodes.length) this.listEl.appendChild(frag);
    this._empty(this.listEl.childElementCount === 0);
  }
}
