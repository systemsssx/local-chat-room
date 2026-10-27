/** ui.js —— 最小的 DOM / 格式化工具。
 *
 * 一条纪律：**用户内容一律用 textContent 塞进去，绝不拼 innerHTML**
 * （房间名、消息正文、文件名都是别人能控制的字符串，拼 HTML 等于开 XSS 的门）。
 */

export const $ = (id) => document.getElementById(id);

/** el('div', {class:'x', text:'内容'}, [子元素…]) */
export function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null) continue;
    if (key === 'text') node.textContent = value;
    else if (key === 'html') node.innerHTML = value;          // 只给固定文案用
    else if (key === 'class') node.className = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) if (child) node.appendChild(child);
  return node;
}

/** 清空一个容器 */
export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

const pad = (n) => String(n).padStart(2, '0');

export function fmtTime(ms) {
  const d = new Date(ms);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function startOfDay(ms) {
  const d = new Date(ms);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

/** 同一天的消息归到一组，用来插「今天 / 昨天 / 10月6日」分隔条 */
export function dayKey(ms) {
  return String(startOfDay(ms));
}

export function fmtDay(ms) {
  const today = startOfDay(Date.now());
  const day = startOfDay(ms);
  if (day === today) return '今天';
  if (day === today - 86400000) return '昨天';
  const d = new Date(ms);
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return sameYear ? `${d.getMonth() + 1} 月 ${d.getDate()} 日`
                  : `${d.getFullYear()} 年 ${d.getMonth() + 1} 月 ${d.getDate()} 日`;
}

/** 房间列表右侧那种短时间：「14:09」「昨天」「周三」「10/6」 */
export function fmtShort(ms) {
  if (!ms) return '';
  const today = startOfDay(Date.now());
  const day = startOfDay(ms);
  if (day === today) return fmtTime(ms);
  if (day === today - 86400000) return '昨天';
  if (today - day < 7 * 86400000) return '周' + '日一二三四五六'[new Date(ms).getDay()];
  const d = new Date(ms);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

export function fmtBytes(n) {
  if (!n && n !== 0) return '';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/** 头像里那个字：中文取第一个字，英文取首字母大写 */
export function initial(name) {
  const text = String(name || '?').trim();
  return text ? text[0].toUpperCase() : '?';
}

/** 所有要显示给人看的文案都从这里过一道。
 *
 * 为什么需要它：`element.textContent = undefined` 在浏览器里会**变成字符串 "undefined"**
 * （textContent 的 setter 会把值 ToString），界面上就出现一个红框写着 "undefined"。
 * 所以赋值前挡住空值，并把现场打到控制台 —— 兜底文案遮住的是显示，不是问题本身。
 */
export function displayText(value, fallback = '') {
  if (value === undefined || value === null) {
    console.error('[文案] 收到空值，已用兜底文案顶上。原始值：', value, '\n调用栈：', new Error().stack);
    return fallback;
  }
  const text = String(value);
  return text === 'undefined' || text === 'null' ? fallback : text;
}

let toastTimer = null;
export function toast(message, kind = '') {
  const node = $('toast');
  if (!node) return;
  node.textContent = displayText(message, '出了点问题，详情看控制台');
  node.className = 'toast show' + (kind ? ' ' + kind : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.className = 'toast'; }, 2600);
}

/** file 的扩展名 → 文件卡片左上角那两三个字母 */
export function extLabel(name) {
  const ext = String(name || '').split('.').pop();
  return (ext && ext.length <= 4 ? ext : 'FILE').toUpperCase();
}
