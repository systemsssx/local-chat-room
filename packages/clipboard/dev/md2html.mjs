/**
 * dev/md2html.mjs —— 够用的 markdown → HTML 渲染器（零依赖）
 *
 * 为什么自己写而不装 marked：整个项目的原则是**前端/工具零依赖**，
 * 而且这里要渲染的文本很特殊（会话原文里有 ASCII 结构图、有大量表格和代码块），
 * 标准渲染器反而会在"段落合并"这一步把结构图压扁。
 *
 * 由两个脚本共用：
 *   · dev/make-transcript.mjs  会话记录（按说话人分色）
 *   · dev/make-doc.mjs         普通文档（带目录侧栏）
 *
 * 支持：围栏代码块 / 标题 / 表格 / 引用 / 有序无序列表 / 分隔线 / 段落
 *       行内：`code`、**粗**、*斜*、[文字](链接)
 */

export const esc = (s) => String(s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

export const slug = (s) => String(s).toLowerCase()
  .replace(/<[^>]+>/g, '')
  .replace(/[^\w\u4e00-\u9fa5]+/g, '-')
  .replace(/^-+|-+$/g, '') || 'h';

/** 行内：`code` / **粗** / *斜* / [文字](链接) */
export function inline(s) {
  let t = esc(s);
  // 先把行内代码抠出来占位 —— 否则代码里的 * _ [ ] 会被后面的规则误伤，
  // 而且 `**` 这种"写在代码里的标记"会漏出来没被渲染。
  const codes = [];
  t = t.replace(/`([^`]+)`/g, (_, c) => `\u0000${codes.push(c) - 1}\u0000`);
  t = t.replace(/\*\*([\s\S]+?)\*\*/g, '<strong>$1</strong>');
  t = t.replace(/(^|[^*])\*([^*\s][^*]*?)\*/g, '$1<em>$2</em>');
  t = t.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  t = t.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[+i]}</code>`);
  return t;
}

const splitRow = (line) =>
  line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());

/**
 * 把被折行的段落接回一行。
 * ⚠️ 不能无脑用空格接：中文之间加空格会多出一堆莫名其妙的空隙。
 * 规则：两侧都是中日韩文字（或标点）就直接接，否则中间补一个空格。
 */
const CJK = /[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]/;
function joinSoftWrapped(lines) {
  return lines.reduce((acc, line) => {
    if (!acc) return line;
    const a = acc[acc.length - 1];
    const b = line[0] ?? '';
    return acc + (CJK.test(a) || CJK.test(b) ? '' : ' ') + line;
  }, '');
}

/**
 * @param {string} md
 * @param {object} [opts]
 * @param {boolean} [opts.preserveNewlines=true] 段落保留换行（会话原文里有 ASCII 图，不保留会被压扁）
 * @param {(h:{level:number,text:string,id:string})=>void} [opts.onHeading] 收集标题用于生成目录
 */
export function mdToHtml(md, opts = {}) {
  const preserve = opts.preserveNewlines !== false;
  const lines = String(md).split('\n');
  const out = [];
  const listStack = [];
  let para = [];
  let hCount = 0;
  let i = 0;

  const flushPara = () => {
    if (para.length) {
      out.push(`<p>${inline(preserve ? para.join('\n') : joinSoftWrapped(para))}</p>`);
      para = [];
    }
  };
  const closeLists = () => {
    while (listStack.length) out.push(listStack.pop() === 'ul' ? '</ul>' : '</ol>');
  };

  while (i < lines.length) {
    const line = lines[i];

    // 围栏代码块（必须先判，里面的一切原样保留）
    const fence = line.match(/^\s*(`{3,}|~{3,})/);
    if (fence) {
      flushPara(); closeLists();
      const ch = fence[1][0];
      const buf = [];
      i++;
      while (i < lines.length && !new RegExp(`^\\s*${ch}{3,}`).test(lines[i])) { buf.push(lines[i]); i++; }
      i++;
      out.push(`<pre class="code"><code>${esc(buf.join('\n'))}</code></pre>`);
      continue;
    }

    if (!line.trim()) { flushPara(); closeLists(); i++; continue; }

    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { flushPara(); closeLists(); out.push('<hr>'); i++; continue; }

    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      flushPara(); closeLists();
      const lv = Math.min(h[1].length + (opts.headingOffset ?? 0), 6);
      const text = inline(h[2]);
      const id = `h${++hCount}-${slug(h[2])}`;
      opts.onHeading?.({ level: lv, text: h[2], id });
      out.push(`<h${lv} class="md-h" id="${id}">${text}</h${lv}>`);
      i++; continue;
    }

    if (/^\s*\|/.test(line) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      flushPara(); closeLists();
      const head = splitRow(line);
      i += 2;
      const rows = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) { rows.push(splitRow(lines[i])); i++; }
      out.push('<div class="tw"><table><thead><tr>'
        + head.map((c) => `<th>${inline(c)}</th>`).join('')
        + '</tr></thead><tbody>'
        + rows.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join('')}</tr>`).join('')
        + '</tbody></table></div>');
      continue;
    }

    if (/^\s*>\s?/.test(line)) {
      flushPara(); closeLists();
      const buf = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) { buf.push(lines[i].replace(/^\s*>\s?/, '')); i++; }
      out.push(`<blockquote>${mdToHtml(buf.join('\n'), { ...opts, onHeading: undefined })}</blockquote>`);
      continue;
    }

    const li = line.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
    if (li) {
      flushPara();
      const type = /^\d/.test(li[2]) ? 'ol' : 'ul';
      while (listStack.length && listStack[listStack.length - 1] !== type) out.push(listStack.pop() === 'ul' ? '</ul>' : '</ol>');
      if (!listStack.length) { listStack.push(type); out.push(type === 'ul' ? '<ul>' : '<ol>'); }
      let text = li[3];
      i++;
      // 列表项的续行（缩进两个空格以上、且不是新的列表项）—— 同样保留换行
      while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*([-*+]|\d+[.)])\s/.test(lines[i])) {
        text += preserve ? '\n' + lines[i].trim() : lines[i].trim(); i++;
      }
      out.push(`<li>${inline(text)}</li>`);
      continue;
    }

    para.push(line.trim());
    i++;
  }
  flushPara(); closeLists();
  return out.join('\n');
}

/* ══════════════ 共用的页面骨架与样式 ══════════════ */
export const BASE_CSS = `
  :root{
    --bg:#FBFAF8; --card:#FFFFFF; --tx:#22262C; --tx2:#5B6472; --tx3:#8A93A0;
    --line:#E6E3DE; --code-bg:#F4F2EE; --accent:#B45309; --link:#1D4ED8;
  }
  *{box-sizing:border-box}
  html{scroll-behavior:smooth}
  body{
    margin:0;background:var(--bg);color:var(--tx);
    font-family:'Segoe UI','Microsoft YaHei',system-ui,-apple-system,sans-serif;
    font-size:15px;line-height:1.75;
  }
  .body>*:first-child{margin-top:0}
  .body>*:last-child{margin-bottom:0}
  .body p{margin:10px 0;white-space:pre-wrap}          /* pre-wrap：保留原文换行（ASCII 图靠它） */
  .body li{white-space:pre-wrap}
  .body h3,.body h4,.body h5,.body h6{margin:18px 0 8px;font-size:15px;font-weight:650}
  .body h3{font-size:16px}
  .body ul,.body ol{margin:10px 0;padding-left:26px}
  .body li{margin:4px 0}
  .body hr{border:0;border-top:1px solid var(--line);margin:22px 0}
  .body blockquote{margin:10px 0;padding:8px 14px;background:var(--code-bg);
                   border-left:3px solid var(--line);border-radius:0 8px 8px 0;
                   color:var(--tx2);font-size:13.5px}
  .body blockquote p{margin:4px 0}
  .body code{background:var(--code-bg);padding:1.5px 6px;border-radius:5px;
             font-family:ui-monospace,Consolas,'Cascadia Mono',monospace;font-size:13px}
  .body pre.code{background:#F7F5F1;border:1px solid var(--line);border-radius:10px;
                 padding:14px 16px;overflow-x:auto;margin:12px 0}
  .body pre.code code{background:none;padding:0;font-size:12.5px;line-height:1.65;white-space:pre}
  .body .tw{overflow-x:auto;margin:12px 0}
  .body table{border-collapse:collapse;font-size:13.5px;width:100%;margin:0}
  .body th,.body td{border:1px solid var(--line);padding:7px 11px;text-align:left;vertical-align:top}
  .body th{background:var(--code-bg);font-weight:600;white-space:nowrap}
  .body a{color:var(--link);text-decoration:none;border-bottom:1px solid rgba(29,78,216,.3)}
  .body a:hover{border-bottom-color:var(--link)}
  .body strong{font-weight:650}
  header.top{
    position:sticky;top:0;z-index:10;background:rgba(251,250,248,.94);
    backdrop-filter:blur(8px);border-bottom:1px solid var(--line);
    padding:12px 22px;display:flex;align-items:center;gap:16px;flex-wrap:wrap;
  }
  header.top .t{font-weight:650;font-size:15px}
  @media print{
    header.top,.toc{display:none!important}
    body{font-size:11.5pt;background:#fff} .intro{display:none}
    .body table,.body pre.code{break-inside:avoid}
  }
`;
