/**
 * render.js —— 信息窗口的渲染层
 *
 * 只认 store：append(新增消息) / reset(换房间)。
 * 因为 store 按 seq 去重，这里可以放心地"只追加不重排"，切换传输时窗口自然连续。
 * 增量追加（记住 lastSeq）比整表重绘更稳：图片不会被重新加载、滚动位置不会跳。
 *
 * ⚠️ 两个元素别搞混：
 *   listEl   = #streamList —— 消息往里塞（它自己不滚动，只是长高）
 *   scrollEl = #msgs       —— 真正带 overflow-y:auto 的滚动容器
 *   只给 listEl 设 scrollTop 是**没有任何效果**的（之前就是这么写的，所以自动滚动一直没生效）。
 *
 * 滚动策略（聊天的通用做法）：
 *   · 你本来就在底部 → 新消息来了自动跟到底部
 *   · 你翻上去看历史了 → **不硬拽你下来**，改成浮出一个「N 条新消息 ↓」按钮
 *   · 首次载入历史 → 直接到底部
 */
import { el, fmtTime, fmtSize } from './ui.js';
import { clientId, nickname, shortIp } from './session.js';

const TP = { polling: 'POLL', sse: 'SSE', ws: 'WS' };

/** 距离底部多少像素以内算"还在底部"（留点余量，手指滑动不至于太敏感） */
const STICK_SLACK = 80;

export function createStreamRenderer({ listEl, scrollEl, store, onEmptyChange }) {
  let lastSeq = 0;
  let primed = false;        // 首次载入历史时强制到底部
  let awayCount = 0;         // 用户翻上去之后，又来了几条
  let sticking = false;      // 当前是否"贴着底部"跟随新消息

  /* 「N 条新消息 ↓」悬浮按钮（挂在滚动容器里，用 sticky 贴着底部） */
  const jump = el('button', {
    class: 'jump-bottom',
    type: 'button',
    onclick: () => scrollToBottom(),
  }, ['新消息 ↓']);
  scrollEl.append(jump);

  function atBottom() {
    return scrollEl.scrollHeight - scrollEl.scrollTop - scrollEl.clientHeight <= STICK_SLACK;
  }

  function scrollToBottom() {
    sticking = true;
    awayCount = 0;
    jump.classList.remove('show');
    scrollEl.scrollTop = scrollEl.scrollHeight;
  }

  /**
   * 内容高度会变 —— 图片是异步加载的，加载完行高才定下来。
   * 没有这个监听的话：滚到底部的那一刻图片还没加载，等高线定下来人就"悬"在半空了。
   * 所以只要还贴着底部，高度一变就再跟一次。
   */
  const heightWatcher = new ResizeObserver(() => {
    if (sticking) scrollEl.scrollTop = scrollEl.scrollHeight;
  });
  heightWatcher.observe(listEl);

  // 用户自己滑动：滑到底部就恢复跟随，滑上去就停止跟随并把提示收掉
  scrollEl.addEventListener('scroll', () => {
    sticking = atBottom();
    if (sticking && jump.classList.contains('show')) {
      awayCount = 0;
      jump.classList.remove('show');
    }
  }, { passive: true });

  /* ---------- 单条消息 ---------- */

  function isMine(msg) {
    return msg.clientId === clientId();
  }

  function renderRail(msg) {
    const mine = isMine(msg);
    const tags = [];
    if (msg.transport) tags.push(el('span', { class: 'tp tp-' + msg.transport, text: TP[msg.transport] ?? msg.transport }));
    if (msg.source && msg.source.startsWith('clipboard')) tags.push(el('span', { class: 'tp src', text: 'CLIP' }));
    if (msg.source === 'history-restore') tags.push(el('span', { class: 'tp src', text: 'RESTORE' }));

    return el('div', { class: 'm-rail' }, [
      el('div', { class: 'm-name', text: mine ? `${msg.nickname} · 本机` : msg.nickname }),
      el('div', { class: 'm-ip', title: msg.ip ?? '', text: msg.ip ?? '' }),
      el('div', { class: 'm-time', text: fmtTime(msg.at) }),
      tags.length ? el('div', { class: 'm-tags' }, tags) : null,
    ]);
  }

  function renderImageBody(msg) {
    const img = el('img', {
      class: 'thumb-img',
      src: msg.file.url,
      alt: msg.file.name,
      loading: 'lazy',
    });
    return el('div', { class: 'm-body' }, [
      el('a', { class: 'thumb', href: msg.file.url, target: '_blank', rel: 'noopener' }, [img]),
      el('div', { class: 'fileline' }, [
        el('span', { class: 'mf-name', text: msg.file.name }),
        el('span', { class: 'mf-meta', text: `${msg.file.width ? msg.file.width + ' × ' + msg.file.height + ' · ' : ''}${fmtSize(msg.file.size)}` }),
        el('a', { class: 'mf-btn', href: msg.file.url, download: msg.file.name }, ['下载']),
      ]),
    ]);
  }

  function renderFileBody(msg) {
    const ext = (msg.file.name.split('.').pop() || '').toUpperCase().slice(0, 3);
    const body = [
      el('div', { class: 'filecard' }, [
        el('span', { class: 'mf-ico', text: ext || 'FILE' }),
        el('span', { class: 'mf-name', text: msg.file.name }),
        el('span', {
          class: 'mf-meta',
          text: `${fmtSize(msg.file.size)}${msg.file.lines ? ' · ' + msg.file.lines + ' 行' : ''}${msg.file.encoding ? ' · ' + msg.file.encoding : ''}`,
        }),
        el('a', { class: 'mf-btn', href: msg.file.url, download: msg.file.name }, ['下载']),
      ]),
    ];
    if (msg.file.preview) body.push(el('pre', { class: 'code', text: msg.file.preview }));
    return el('div', { class: 'm-body' }, body);
  }

  function renderTextBody(msg) {
    return el('div', { class: 'm-body' }, [el('div', { class: 'm-text', text: msg.text ?? '' })]);
  }

  function renderMessage(msg) {
    return el('div', { class: 'msg' + (isMine(msg) ? ' me' : ''), 'data-seq': msg.seq }, [
      renderRail(msg),
      msg.type === 'image' ? renderImageBody(msg)
        : msg.type === 'file' ? renderFileBody(msg)
        : renderTextBody(msg),
    ]);
  }

  /* ---------- 追加 ---------- */

  function append(messages) {
    const fresh = messages
      .filter((m) => typeof m.seq === 'number' && m.seq > lastSeq)
      .sort((a, b) => a.seq - b.seq);
    if (!fresh.length) return 0;

    const stick = !primed || atBottom();     // 首次载入 或 本来就在底部 → 跟着走

    const frag = document.createDocumentFragment();
    for (const m of fresh) frag.append(renderMessage(m));
    listEl.append(frag);
    lastSeq = fresh[fresh.length - 1].seq;

    if (stick) {
      scrollToBottom();                      // 先滚一次
      requestAnimationFrame(() => { if (sticking) scrollToBottom(); });   // 排版完再补一次
      primed = true;
    } else {
      sticking = false;
      awayCount += fresh.length;
      jump.textContent = `${awayCount} 条新消息 ↓`;
      jump.classList.add('show');
    }

    onEmptyChange?.(store.count > 0);
    return fresh.length;
  }

  function reset() {
    listEl.replaceChildren();
    lastSeq = 0;
    primed = false;
    awayCount = 0;
    sticking = false;
    jump.classList.remove('show');
    onEmptyChange?.(false);
  }

  return { append, reset, scrollToBottom, get lastSeq() { return lastSeq; } };
}
