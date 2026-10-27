/**
 * ui.js —— 公共小工具：建 DOM、格式化、轻提示
 */

/** 极简建元素：el('div', {class:'x'}, [child, 'text']) */
export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k === 'html') node.innerHTML = v;
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

export function fmtTime(ms) {
  if (!ms) return '';
  return new Date(ms).toLocaleTimeString('zh-CN', { hour12: false });
}

export function fmtSize(bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  return (n / 1024 / 1024).toFixed(2) + ' MB';
}

let toastTimer = null;
export function toast(message, kind = 'info') {
  let box = document.getElementById('toast');
  if (!box) {
    box = el('div', { id: 'toast', class: 'toast' });
    document.body.append(box);
  }
  box.textContent = message;
  box.className = 'toast show ' + kind;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.className = 'toast ' + kind; }, 2600);
}
