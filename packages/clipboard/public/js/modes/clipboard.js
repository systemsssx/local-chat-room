/**
 * 输入模式二 · 剪贴板监听（文本 + 图片）
 *
 * 底栏没有输入框，换成一条监听状态条。
 *
 * 三条硬事实（也是这个功能最容易踩的坑）：
 *  1. 浏览器**没有剪贴板变化事件**，只能定时读 —— 本质还是轮询，只不过轮的是本机剪贴板；
 *  2. navigator.clipboard 只在**安全上下文**可用：http://localhost 可以，http://192.168.x.x 不行。
 *     所以这个模式在本机页面上有效，手机走局域网 IP 打开时会被明确告知"非安全上下文"；
 *  3. 读剪贴板还要求**页面处于焦点**并且**用户授权**（Chrome 会弹一次"允许查看剪贴板？"）。
 *
 * 读剪贴板有两个 API，分工不同：
 *   · readText()  —— 只能读文本。Firefox 只支持这个。
 *   · read()      —— 一次拿到剪贴板里的所有类型（文本 + 图片），但 Firefox 不支持。
 *   所以：有 read() 就用它（能拿图片），没有就退回 readText()（只发文本）。
 *
 * 错误分两类，绝不能混成一句"被拒绝"：
 *   · 永久性（hardUnsupported）：没有 API / 非安全上下文 —— 真的用不了，只能换「纯文字」模式；
 *   · 可恢复（未授权 / 未聚焦）：保持轮询 + 给「重试」按钮，用户点允许之后**自己就好**。
 *
 * 回环防护：收到远端文本写进本机剪贴板时打标记，本地监听看到标记就跳过，避免 A→B→A 死循环。
 */
import { el, fmtTime } from '../ui.js';

export const POLL_MS = 800;
export const REMOTE_GUARD_MS = 1500;

const IMAGE_NAME = () => {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `剪贴板图片-${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}.png`;
};

export function createClipboardMode({ mount, send, upload }) {
  let timer = null;
  let lastSeenText = '';          // 上一次读到的文本
  let remoteGuess = '';           // 刚刚由远端写入的文本，用来抑制回环
  let remoteUntil = 0;
  let sending = false;
  let paused = false;

  // 图片去重：大小+类型当"便宜的门"，只在门变了才算哈希 —— 否则每 800ms 给 2MB 做一次 SHA-256 太浪费
  let lastImageCheap = '';
  let lastImageHash = '';

  let hardUnsupported = false;
  let canReadImages = false;      // 浏览器支持 clipboard.read()（能拿图片）
  let permState = 'unknown';
  let lastError = '';

  const dot = el('span', { class: 'ln-dot' });
  const textEl = el('span', { class: 'ln-text', text: '正在监听本机剪贴板' });
  const lastEl = el('span', { class: 'ln-last', text: '最近：—' });
  const retryBtn = el('button', { class: 'ln-toggle', type: 'button', onclick: onRetry }, ['重试']);
  const toggleBtn = el('button', { class: 'ln-toggle', type: 'button', onclick: togglePause }, ['暂停']);
  const warn = el('div', { class: 'comp-hint' });
  const diag = el('div', { class: 'ln-diag' });

  const root = el('div', { class: 'listen' }, [dot, textEl, lastEl, retryBtn, toggleBtn, warn, diag]);

  /* ---------- 状态呈现 ---------- */

  function setState(kind, text) {
    dot.className = 'ln-dot ' + kind;
    if (text) textEl.textContent = text;
  }

  function setWarn(html) {
    warn.innerHTML = html || '';
    warn.style.display = html ? '' : 'none';
  }

  function paintDiag() {
    diag.textContent = `安全上下文 ${window.isSecureContext ? '✓' : '✗'} · `
      + `页面聚焦 ${document.hasFocus() ? '✓' : '✗'} · `
      + `权限 ${permState} · `
      + `图片 ${canReadImages ? '可读' : '不可读（只发文本）'}`
      + (lastError ? ` · 最近错误 ${lastError}` : '');
  }

  function paint() {
    paintDiag();
    if (hardUnsupported) {
      retryBtn.style.display = 'none';
      setState('off', '当前环境不支持读取剪贴板');
      return;
    }
    retryBtn.style.display = '';
    if (paused) { setState('paused', '已暂停监听'); return; }
    if (lastError) { setState('off', '监听暂时中断 —— 点「重试」'); return; }
    setState('live', canReadImages
      ? '正在监听本机剪贴板 · 文字和图片都会自动发到房间'
      : '正在监听本机剪贴板 · 文字会自动发到房间（这个浏览器读不到图片）');
  }

  /* ---------- 能力与权限 ---------- */

  async function refreshPermission() {
    try {
      const st = await navigator.permissions.query({ name: 'clipboard-read' });
      permState = st.state;
      st.onchange = () => { permState = st.state; paint(); };   // 用户去地址栏改权限后界面自动跟着变
    } catch {
      permState = 'unknown';
    }
  }

  async function refreshCapability() {
    // ⚠️ 顺序要紧：非安全上下文里 navigator.clipboard 直接就是 undefined，
    // 先判 API 会误报成"浏览器不支持"，把用户往错方向引。
    if (!window.isSecureContext) {
      hardUnsupported = true;
      setWarn('⚠️ <b>非安全上下文</b>：当前地址不是 localhost/HTTPS，'
            + '浏览器在这种页面上<b>根本不提供</b>剪贴板接口。'
            + '<br>想在本机电脑上用这个模式，请用 <b>http://localhost:8000</b> 打开；'
            + '手机上请改用「纯文字」模式。');
      paint();
      return;
    }
    if (!navigator.clipboard?.readText && !navigator.clipboard?.read) {
      hardUnsupported = true;
      setWarn('⚠️ <b>这个浏览器没有提供剪贴板读取接口</b>，请改用「纯文字」模式。');
      paint();
      return;
    }
    hardUnsupported = false;
    canReadImages = typeof navigator.clipboard.read === 'function';   // Firefox 没有，只能发文本
    await refreshPermission();
    paint();
  }

  /* ---------- 读剪贴板 ---------- */

  function describeError(err) {
    const name = err?.name || 'Error';
    if (name === 'NotAllowedError') {
      if (!document.hasFocus()) {
        return '⚠️ <b>页面没有聚焦</b>：浏览器只在页面处于焦点时允许读剪贴板。'
             + '<br>点一下页面（或点下面的「重试」），再试一次。';
      }
      return '⚠️ <b>浏览器拒绝读取剪贴板</b>：多半是授权弹窗还没点「允许」（点弹窗时页面会失焦，'
           + '这也是常见的卡点）。'
           + '<br>点「重试」，在弹窗里选 <b>允许</b>；或者点地址栏左边的图标 → 剪贴板 → 允许。';
    }
    if (name === 'SecurityError') {
      return '⚠️ 安全策略拒绝（SecurityError）：确认当前地址是 <b>http://localhost</b> 或 HTTPS。';
    }
    return `⚠️ 读取剪贴板失败（${name}）：${err?.message ?? err}`;
  }

  /** 环境不支持读图片时的浏览器会抛这个 */
  const isTypeUnsupported = (err) => err?.name === 'TypeError' || err?.name === 'DataError';

  /** 读一次剪贴板，返回 { kind:'image', blob } / { kind:'text', text } / null（读失败） */
  async function readClipboard() {
    try {
      let payload = null;

      if (canReadImages) {
        const items = await navigator.clipboard.read();
        // 优先图片：从网页复制一张图时，剪贴板里通常同时有 text/html 和 image/png
        for (const item of items) {
          const type = item.types.find((t) => t.startsWith('image/'));
          if (type) { payload = { kind: 'image', blob: await item.getType(type), type }; break; }
        }
        if (!payload) {
          for (const item of items) {
            if (item.types.includes('text/plain')) {
              payload = { kind: 'text', text: await (await item.getType('text/plain')).text() };
              break;
            }
          }
        }
        // 从资源管理器复制文件时，剪贴板里是 text/uri-list（file:/// 路径）。
        // 网页**拿不到文件内容**（安全边界），但至少能识别出来并告诉用户走 Ctrl+V。
        if (!payload) {
          const uriItem = items.find((it) => it.types.includes('text/uri-list'));
          if (uriItem) {
            const uri = await (await uriItem.getType('text/uri-list')).text();
            if (/file:\/\//i.test(uri)) payload = { kind: 'files', uri };
          }
        }
        if (!payload) payload = { kind: 'text', text: '' };
      } else {
        payload = { kind: 'text', text: await navigator.clipboard.readText() };
      }

      // 读成功 → 把之前那行错误警告**一起清掉**
      // （只清 lastError 而不清警告，会留下一条"明明已经好了还在报错"的僵尸提示。）
      if (lastError) {
        lastError = '';
        setWarn('');
        lastEl.textContent = '最近：已恢复监听';
        paint();
      }
      return payload;
    } catch (err) {
      const name = err?.name || 'Error';
      if (name !== lastError) {           // 同一种错误不重复刷屏
        lastError = name;
        setWarn(describeError(err));
        paint();
      }
      return null;
    }
  }

  /** 图片指纹：先比"大小+类型"（便宜），变了才算 SHA-256（准） */
  async function fingerprint(blob) {
    const cheap = `${blob.type}:${blob.size}`;
    if (cheap === lastImageCheap) return lastImageHash;
    let hash = cheap;
    try {
      const buf = await blob.arrayBuffer();
      const digest = await crypto.subtle.digest('SHA-256', buf);
      hash = [...new Uint8Array(digest)].slice(0, 12).map((b) => b.toString(16).padStart(2, '0')).join('');
    } catch {
      hash = cheap;                       // 没法算哈希就退回用大小+类型（够用）
    }
    lastImageCheap = cheap;
    lastImageHash = hash;
    return hash;
  }

  /** 用户在状态条上点「重试」—— 用户手势，触发授权弹窗最可靠的时机 */
  async function onRetry() {
    setWarn('');
    lastError = '';
    await refreshCapability();
    if (hardUnsupported) return;
    const payload = await readClipboard();
    if (payload) {
      lastSeenText = payload.kind === 'text' ? payload.text : '';
      setWarn('');
      lastEl.textContent = '最近：已获授权，开始监听';
      paint();
    }
  }

  function togglePause() {
    paused = !paused;
    toggleBtn.textContent = paused ? '继续' : '暂停';
    paint();
  }

  /** 页面重新获得焦点 → 立刻试一次，不用等下一个 800ms 周期 */
  function onWindowFocus() {
    if (!paused && !hardUnsupported) tick();
  }

  /* ---------- 发送 ---------- */

  async function sendText(text) {
    if (text === lastSeenText) return;
    lastSeenText = text;
    if (!text.trim()) return;

    if (text === remoteGuess && performance.now() < remoteUntil) {
      lastEl.textContent = '最近：' + fmtTime(Date.now()) + '（远端写入，已跳过）';
      return;
    }

    sending = true;
    try {
      await send({ type: 'text', text, source: 'clipboard' });
      lastEl.textContent = '最近：' + fmtTime(Date.now()) + ' · 已发送文字';
    } catch (err) {
      lastEl.textContent = '最近：发送失败';
      setWarn('⚠️ 发送失败：' + (err?.message ?? err));
    } finally {
      sending = false;
    }
  }

  async function sendImage(blob, type) {
    const hash = await fingerprint(blob);
    if (lastEl.dataset.lastBlob === hash) return;       // 同一张图，别重复发
    sending = true;
    try {
      const { kind, file } = await uploadAndSend(blob, IMAGE_NAME(), type);
      lastEl.dataset.lastBlob = hash;
      lastEl.textContent = `最近：${fmtTime(Date.now())} · 已发送${kind === 'image' ? '图片' : '文件'} ${file.name}`;
    } catch (err) {
      lastEl.textContent = '最近：图片发送失败';
      setWarn('⚠️ 图片发送失败：' + (err?.message ?? err) + '（单张图片上限 2MB）');
    } finally {
      sending = false;
    }
  }

  /** 上传 + 把返回的 file 对象当消息发出去。图片走 image 类型，其它走 file 类型 */
  async function uploadAndSend(blob, name, type) {
    const file = new File([blob], name, { type: type || blob.type || 'application/octet-stream' });
    const res = await upload(file);
    const kind = (res.file?.mime || '').startsWith('image/') ? 'image' : 'file';
    await send({ type: kind, file: res.file, text: res.file.name, source: 'clipboard' });
    return { kind, file: res.file };
  }

  /**
   * 用户在页面上按 Ctrl+V —— **这是唯一能把"文件"传进来的通道**。
   *
   * navigator.clipboard.read() 拿不到从资源管理器复制的文件（浏览器的安全边界：
   * 网页不能随便读你磁盘上的文件），但 paste 事件的 clipboardData.files 可以。
   * 所以文件走 Ctrl+V，文字和图片仍然由轮询自动捕获。
   */
  async function onPaste(e) {
    const dt = e.clipboardData;
    if (!dt || sending) return;

    const files = [...(dt.files ?? [])];
    if (!files.length) {
      for (const item of dt.items ?? []) {
        if (item.kind === 'file') { const f = item.getAsFile(); if (f) files.push(f); }
      }
    }
    if (!files.length) return;            // 纯文字粘贴：交给轮询处理，别重复发
    e.preventDefault();

    sending = true;
    const names = [];
    try {
      for (const f of files) {
        const { kind, file } = await uploadAndSend(f, f.name || IMAGE_NAME(), f.type);
        names.push(`${file.name}（${kind === 'image' ? '图片' : '文件'}）`);
      }
      lastEl.textContent = `最近：${fmtTime(Date.now())} · 已发送 ${names.join('、')}`;
      setWarn('');
    } catch (err) {
      lastEl.textContent = '最近：粘贴发送失败';
      setWarn('⚠️ 粘贴的文件发送失败：' + (err?.message ?? err)
            + '<br>只接受图片和纯文本文件，单个上限 2MB。');
    } finally {
      sending = false;
    }
  }

  /* ---------- 轮询 ---------- */

  async function tick() {
    if (paused || sending || hardUnsupported) return;
    if (document.visibilityState !== 'visible') return;
    if (!document.hasFocus()) { paintDiag(); return; }   // 没聚焦就别去撞墙，省得刷一堆报错

    const payload = await readClipboard();
    if (!payload) return;

    if (payload.kind === 'image') { await sendImage(payload.blob, payload.type); return; }

    if (payload.kind === 'files') {
      // 识别到剪贴板里是文件，但内容读不到 —— 明确说清该怎么办，而不是静默什么都不做
      const names = payload.uri.split(/\r?\n/).filter((s) => s && !s.startsWith('#')).length;
      setWarn(`⚠️ 剪贴板里是 <b>${names || ''} 个文件</b>（从资源管理器复制的？）。`
            + '浏览器不允许网页直接读取本地文件内容 —— 这是安全边界，不是本项目的限制。'
            + '<br><b>在页面上按 Ctrl+V</b> 就能发出去（这条路浏览器允许）。');
      setState('paused', '剪贴板里是文件 —— 按 Ctrl+V 发送');
      lastEl.textContent = '最近：检测到文件，等待 Ctrl+V';
      return;
    }

    await sendText(payload.text);
  }

  /* ---------- 对外接口 ---------- */

  return {
    id: 'clipboard',
    label: '剪贴板读取',
    hint: '监听本机剪贴板，文字和图片自动发到房间；文件请在本页面按 Ctrl+V（仅 localhost 可用）',

    attach() {
      mount.replaceChildren(root);
      setWarn('');
      lastError = '';
      paint();

      // attach() 就在用户点「剪贴板读取」的同一个手势里：
      // 这里立刻读一次，能顺势把浏览器的授权弹窗带出来。
      refreshCapability().then(() => {
        if (hardUnsupported) return;
        readClipboard().then((payload) => {
          if (payload?.kind === 'text') lastSeenText = payload.text;
        });
      });

      if (timer) clearInterval(timer);
      timer = setInterval(tick, POLL_MS);
      window.addEventListener('focus', onWindowFocus);
      document.addEventListener('paste', onPaste);      // 文件只能走这条路
    },

    detach() {
      if (timer) clearInterval(timer);
      timer = null;
      window.removeEventListener('focus', onWindowFocus);
      document.removeEventListener('paste', onPaste);
    },

    /**
     * 由 main.js 在收到远端消息时调用（**只在剪贴板模式激活时**）。
     * 只处理文本 —— 图片回写本机剪贴板需要 ClipboardItem 写权限，且容易和本地图片打架，先不做。
     */
    onRemoteMessage(msg) {
      if (msg?.type !== 'text' || !msg.text) return;
      remoteGuess = msg.text;
      remoteUntil = performance.now() + REMOTE_GUARD_MS;
      lastSeenText = msg.text;
      navigator.clipboard?.writeText?.(msg.text).catch(() => { /* 无权限就算了，不影响显示 */ });
      lastEl.textContent = '最近：' + fmtTime(msg.at) + ' · 远端写入本机剪贴板';
    },
  };
}
