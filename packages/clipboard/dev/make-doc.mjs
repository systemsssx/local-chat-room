/**
 * dev/make-doc.mjs —— 把一份 markdown 文档渲染成**单文件 HTML**（带目录侧栏）
 *
 * 为什么不直接用 markdown：
 *   markdown 标准里没有颜色和版式控制，而且很多阅读器（浏览器直接打开、记事本）
 *   根本不渲染 markdown，看到的就是一堆 `##` 和 `**`。
 *   HTML 则双击就能看：版式、目录、表格、代码块、打印样式全都在。
 *
 * 用法：
 *   node dev/make-doc.mjs <输入.md> <输出.html> [副标题]
 */
import fs from 'node:fs';
import path from 'node:path';
import { BASE_CSS, esc, mdToHtml } from './md2html.mjs';

const [inPath, outPath, subtitle] = process.argv.slice(2);
if (!inPath || !outPath) {
  console.error('用法: node dev/make-doc.mjs <输入.md> <输出.html> [副标题]');
  process.exit(1);
}

const md = fs.readFileSync(inPath, 'utf8');

/* 取出第一个 # 当页面标题（剩下的正文里不再重复它） */
const titleMatch = md.match(/^#\s+(.+)$/m);
const title = titleMatch ? titleMatch[1].trim() : path.basename(inPath, '.md');
let body = titleMatch ? md.replace(titleMatch[0], '').trim() : md.trim();

/* 标题后面紧跟的引用块当"导语"，单独排版 */
let lead = '';
const leadMatch = body.match(/^(?:>\s?.*\n?)+/);
if (leadMatch) {
  lead = leadMatch[0].split('\n')
    .map((l) => l.replace(/^\s*>\s?/, ''))
    .join('\n').trim();
  body = body.slice(leadMatch[0].length).trim();
}

/* 渲染 + 顺手收集 h2 做目录
   preserveNewlines:false —— 文档类要把源码里的折行接回一行，
   否则每个段落会显示成好几行短句（看着像乱空行）。会话记录那份是反过来的，见 make-transcript.mjs。 */
const toc = [];
const bodyHtml = mdToHtml(body, {
  preserveNewlines: false,
  onHeading: (h) => { if (h.level === 2) toc.push(h); },
});

const tocHtml = toc.length
  ? `<nav class="toc"><div class="toc-t">目录</div><ol>${
      toc.map((h) => `<li><a href="#${h.id}">${esc(h.text)}</a></li>`).join('')
    }</ol></nav>`
  : '';
const html = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(title)}</title>
<style>
${BASE_CSS}
  :root{--accent:#B45309}
  header.top .sub{color:var(--tx3);font-size:12.5px}

  .layout{max-width:1180px;margin:0 auto;padding:26px 22px 90px;
          display:grid;grid-template-columns:236px minmax(0,1fr);gap:40px;align-items:start}
  /* 没有小标题（也就没有目录）时改单栏，正文窄一点更好读 */
  .layout.no-toc{grid-template-columns:minmax(0,1fr);max-width:860px}
  .layout.no-toc .body{font-size:15.5px;line-height:1.95}
  .layout.no-toc .body p{margin:16px 0;text-indent:2em}   /* 中文习惯：段首空两格 */
  .toc{position:sticky;top:72px;max-height:calc(100vh - 100px);overflow-y:auto;
       font-size:13px;background:var(--card);border:1px solid var(--line);
       border-radius:12px;padding:14px 16px}
  .toc-t{font-weight:650;font-size:12px;letter-spacing:.1em;color:var(--tx3);margin-bottom:8px}
  .toc ol{margin:0;padding-left:18px}
  .toc li{margin:6px 0;line-height:1.5}
  .toc a{color:var(--tx2);text-decoration:none;border:0}
  .toc a:hover{color:var(--accent)}

  h1.doc{font-size:27px;line-height:1.35;margin:6px 0 6px;letter-spacing:-.01em}
  .lead{color:var(--tx2);font-size:13.5px;margin:0 0 30px;padding-left:14px;
        border-left:3px solid var(--accent)}
  .lead p{margin:5px 0;white-space:pre-wrap}

  .body h2{font-size:19px;margin:40px 0 14px;padding-bottom:8px;
           border-bottom:2px solid var(--line);color:var(--accent);font-weight:650}
  .body h2:first-child{margin-top:0}
  .body h3,.body h4{color:var(--tx);font-weight:650}
  .body h3{font-size:16px;margin:26px 0 8px}
  .body h4{font-size:14.5px;margin:20px 0 6px}
  .body .md-h{scroll-margin-top:76px}

  @media (max-width:900px){
    .layout{grid-template-columns:minmax(0,1fr);gap:22px;padding:18px 14px 70px}
    .toc{position:static;max-height:none}
    h1.doc{font-size:22px}
  }
</style>
</head>
<body>

<header class="top">
  <span class="t">${esc(title)}</span>
  ${subtitle ? `<span class="sub">${esc(subtitle)}</span>` : ''}
</header>

<div class="layout${toc.length ? '' : ' no-toc'}">
  ${tocHtml}
  <main>
    <h1 class="doc">${esc(title)}</h1>
    ${lead ? `<div class="lead">${mdToHtml(lead)}</div>` : ''}
    <div class="body">${bodyHtml}</div>
  </main>
</div>

</body>
</html>
`;

fs.mkdirSync(path.dirname(path.resolve(outPath)), { recursive: true });
fs.writeFileSync(outPath, html, 'utf8');
console.log(`${path.basename(inPath)} → ${path.basename(outPath)} | ${toc.length} 个章节 | ${(fs.statSync(outPath).size / 1024).toFixed(1)} KB`);
