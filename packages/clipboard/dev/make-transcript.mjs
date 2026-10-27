/**
 * dev/make-transcript.mjs —— 从 DSH 会话原始日志里抽出「完整对话记录」，输出**单文件 HTML**
 *
 * 为什么是 HTML 不是 Markdown：
 *   markdown 标准里没有颜色，颜色只能靠内嵌 HTML；而多数阅读器（浏览器直接打开、记事本）
 *   不渲染内嵌 HTML，就把 <span> 原样显示出来 —— 一片标签，谁都看不懂。
 *   HTML 则**双击就能看**：配色、层级、表格、代码块全都在。
 *
 * 所以这个脚本自己做了一个小的 markdown→HTML 渲染（~120 行），把会话正文渲染成富文本，
 * 页面自包含、不依赖网络、不依赖任何库。
 *
 * 颜色：👤 我 = 琥珀 #D97706（项目主题色）｜🤖 AI = 蓝 #2563EB
 *       AI 回复里的小标题也换成蓝色（渲染时用 .ai .md-h 选中）
 *
 * 输入：session.v4.jsonl.zstd（多 zstd 帧拼接；Node 的 zstd 接口只解第一帧，
 *      所以这里自己走一遍 zstd 帧结构逐帧解压）
 *
 * 用法：
 *   node dev/make-transcript.mjs <日志> <输出html> [--from 关键字] [--skip 起 止] [--to 关键字]
 */
import fs from 'node:fs';
import zlib from 'node:zlib';

// markdown 渲染器抽在 dev/md2html.mjs，和 dev/make-doc.mjs 共用一份
import { BASE_CSS, esc, mdToHtml } from './md2html.mjs';

/* ══════════════════ 1. 多帧 zstd 解压 ══════════════════ */
const MAGIC = 0xfd2fb528;
const SKIP_BASE = 0x184d2a50;

function frameEnd(buf, start) {
  let p = start;
  const magic = buf.readUInt32LE(p);
  if ((magic & 0xfffffff0) === SKIP_BASE) return p + 8 + buf.readUInt32LE(p + 4);
  if (magic !== MAGIC) return -1;
  p += 4;
  const fhd = buf[p++];
  const fcsFlag = fhd >> 6;
  const singleSegment = (fhd >> 5) & 1;
  const dictFlag = fhd & 3;
  const hasChecksum = (fhd >> 2) & 1;
  if (!singleSegment) p += 1;
  p += [0, 1, 2, 4][dictFlag];
  p += fcsFlag === 0 ? (singleSegment ? 1 : 0) : [0, 2, 4, 8][fcsFlag];
  for (;;) {
    const h = buf[p] | (buf[p + 1] << 8) | (buf[p + 2] << 16);
    const last = h & 1, btype = (h >> 1) & 3, bsize = h >> 3;
    p += 3;
    if (btype === 0) p += bsize;
    else if (btype === 1) p += 1;
    else if (btype === 2) p += bsize;
    else throw new Error('保留块类型');
    if (last) break;
  }
  if (hasChecksum) p += 4;
  return p;
}

function readLog(file) {
  const buf = fs.readFileSync(file);
  const parts = [];
  let p = 0, frames = 0;
  while (p < buf.length) {
    const end = frameEnd(buf, p);
    if (end < 0 || end > buf.length) throw new Error(`帧边界异常 p=${p}`);
    parts.push(zlib.zstdDecompressSync(buf.subarray(p, end)));
    p = end;
    frames++;
  }
  const recs = [];
  for (const line of Buffer.concat(parts).toString('utf8').split('\n')) {
    if (!line.trim()) continue;
    try { recs.push(JSON.parse(line)); } catch { /* 跳过坏行 */ }
  }
  return { recs, frames };
}


/* ══════════════════ 3. 工具调用摘要 ══════════════════ */
function toolSummary(name, argsRaw) {
  let a = {};
  try { a = JSON.parse(argsRaw ?? '{}'); } catch { /* 忽略 */ }
  const short = (s, n = 110) => String(s ?? '').replace(/\s+/g, ' ').slice(0, n);
  switch (name) {
    case 'read':   return `read(${short(a.file_path)})`;
    case 'write':  return `write(${short(a.file_path)})`;
    case 'edit':   return `edit(${short(a.file_path)})`;
    case 'glob':   return `glob(${short(a.pattern)} @ ${short(a.path, 60)})`;
    case 'grep':   return `grep(${short(a.pattern, 60)})`;
    case 'pwsh':   return `pwsh(${short(a.description ?? a.command, 90)})`;
    case 'skill':  return `skill(${short(a.name, 40)})`;
    case 'todo_write': return `todo_write(${(a.todos ?? []).length} 项)`;
    case 'present':    return `present(${(a.files ?? []).map((f) => f.path?.split(/[\\/]/).pop()).join(', ')})`;
    case 'job_kill':   return `job_kill(${short(a.job_id, 30)})`;
    default:       return `${name}(${short(argsRaw, 90)})`;
  }
}

/* ══════════════════ 4. 参数 ══════════════════ */
const argv = process.argv.slice(2);
const logPath = argv[0];
const outPath = argv[1];
if (!logPath || !outPath || logPath.startsWith('--')) {
  console.error('用法: node dev/make-transcript.mjs <日志> <输出html> [--from 关键字] [--skip 起 止] [--to 关键字]');
  process.exit(1);
}
const kwAfter = (flag) => { const i = argv.indexOf(flag); return i >= 0 ? argv[i + 1] : null; };
const fromKw = kwAfter('--from');
const toKw = kwAfter('--to');
const si = argv.indexOf('--skip');
const skipFrom = si >= 0 ? argv[si + 1] : null;
const skipTo = si >= 0 ? argv[si + 2] : null;

const { recs, frames } = readLog(logPath);

/* ask_user_question 的答案按 callId 建索引 */
const answers = new Map();
for (const r of recs) {
  if (r.type !== 'tool/result') continue;
  const callId = r.data?.message?.toolCallId;
  const text = r.data?.message?.content?.[0]?.text;
  if (!callId || !text) continue;
  try { answers.set(callId, JSON.parse(text).answers ?? []); } catch { /* 忽略 */ }
}

/* ══════════════════ 5. 摊平成「块」 ══════════════════ */
const blocks = [];
const skipped = { system: 0, reasoning: 0, empty: 0 };
const hit = (obj, kw) => Boolean(kw) && obj.includes(kw);

for (const r of recs) {
  if (r.type === 'user/message') {
    const kind = r.data?.source?.kind;
    const text = (r.data?.content ?? []).filter((c) => c.type === 'text')
      .map((c) => c.text).join('\n\n').trim();
    if (kind !== 'user') { skipped.system++; continue; }
    if (!text) { skipped.empty++; continue; }
    blocks.push({
      who: 'me', text,
      from: hit(text, fromKw), skipFrom: hit(text, skipFrom),
      skipTo: hit(text, skipTo), to: hit(text, toKw),
    });
    continue;
  }

  if (r.type === 'assistant/message') {
    const content = r.data?.message?.content ?? [];
    const texts = content.filter((b) => b.type === 'text' && b.text?.trim()).map((b) => b.text.trim());
    const calls = content.filter((b) => b.type === 'tool-call');
    skipped.reasoning += content.filter((b) => b.type === 'reasoning').length;

    for (const c of calls) {
      if (c.name !== 'ask_user_question') continue;
      let qs = [];
      try { qs = JSON.parse(c.arguments).questions ?? []; } catch { /* 忽略 */ }
      const ans = answers.get(c.id) ?? [];
      const chosen = qs.map((q) => {
        const a = ans.find((x) => x.id === q.id);
        const sel = a?.selected?.length ? a.selected.join(' / ')
          : (a?.custom ? `（自定义）${a.custom}` : '（未选）');
        return `- **${q.question}**\n  → ${sel}`;
      }).join('\n');
      blocks.push({ who: 'me', text: chosen, isChoice: true });
    }

    const others = calls.filter((c) => c.name !== 'ask_user_question');
    if (texts.length || others.length) {
      const body = [];
      if (texts.length) body.push(texts.join('\n\n'));
      if (others.length) body.push(others.map((c) => `> 🔧 ${toolSummary(c.name, c.arguments)}`).join('\n'));
      blocks.push({ who: 'ai', text: body.join('\n\n') });
    }
    if (!texts.length && !calls.length) skipped.empty++;
  }
}

/* 合并「只有工具调用」的 AI 块到上一条 —— 内容一行不删，只是别那么碎 */
const isToolOnly = (b) =>
  b.who === 'ai' && b.text.split('\n').every((l) => !l.trim() || l.startsWith('> 🔧'));
const merged = [];
for (const b of blocks) {
  const prev = merged[merged.length - 1];
  if (isToolOnly(b) && prev?.who === 'ai') { prev.text += '\n' + b.text; continue; }
  merged.push({ ...b });
}

/* ══════════════════ 6. 按 --from / --skip / --to 筛选 ══════════════════ */
let started = !fromKw;
let stopping = false;
let inSkip = false;
const kept = [];
const drop = { before: 0, skip: 0, after: 0 };

for (const b of merged) {
  if (!started) {
    if (b.who === 'me' && b.from) started = true;
    else { drop.before++; continue; }
  }
  if (stopping) { drop.after++; continue; }

  if (b.who === 'me' && b.skipFrom) inSkip = true;
  if (inSkip) {
    if (b.who === 'me' && b.skipTo) inSkip = false;
    else { drop.skip++; continue; }
  }

  if (b.who === 'me' && b.to) { stopping = true; drop.after++; continue; }
  kept.push(b);
}

/* ══════════════════ 7. 渲染 HTML ══════════════════ */
const C = { me: '#B45309', ai: '#1D4ED8' };   // 浅底上更清楚的琥珀 / 蓝

let idx = 0;
const sections = kept.map((b) => {
  idx++;
  const isMe = b.who === 'me';
  const cls = isMe ? 'me' : 'ai';
  const label = isMe ? (b.isChoice ? '👤 我（选择）' : '👤 我') : '🤖 AI';
  return `<section class="${cls}" id="b${idx}">
  <h2><span class="no">${idx}</span><span class="who">${label}</span></h2>
  <div class="body">${mdToHtml(b.text)}</div>
</section>`;
}).join('\n');

const cutLines = [
  fromKw ? `开头「选题与范围讨论」全部去掉（从含 <b>${esc(fromKw)}</b> 那条开始收）` : null,
  skipFrom ? `中间「怎么整理文档」那段去掉（含 <b>${esc(skipFrom)}</b> 到 <b>${esc(skipTo)}</b> 之前）` : null,
  toKw ? `末尾「本次整理文档」的讨论去掉（遇到含 <b>${esc(toKw)}</b> 那条为止）` : null,
].filter(Boolean).map((l) => `<li>${l}</li>`).join('\n      ');

const html = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>完整对话记录 · 协同剪贴板</title>
﻿<style>
${BASE_CSS}
  :root{--me:${C.me};--ai:${C.ai}}

  header.top .legend{display:flex;gap:14px;font-size:13px;margin-left:auto}
  header.top .legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px;vertical-align:-1px}
  header.top .legend .m i{background:var(--me)} header.top .legend .a i{background:var(--ai)}
  header.top .legend .m{color:var(--me)} header.top .legend .a{color:var(--ai)}

  .wrap{max-width:1000px;margin:0 auto;padding:0 22px 90px}

  .intro{background:var(--card);border:1px solid var(--line);border-radius:12px;
         padding:20px 24px;margin:26px 0 34px;font-size:13.5px;color:var(--tx2)}
  .intro h1{margin:0 0 14px;font-size:20px;color:var(--tx)}
  .intro ul{margin:8px 0 14px;padding-left:22px}
  .intro li{margin:4px 0}
  .intro .src{font-family:ui-monospace,Consolas,monospace;font-size:12px;color:var(--tx3);word-break:break-all}

  section{border-left:4px solid var(--line);padding:2px 0 2px 18px;margin:30px 0}
  section.me{border-color:var(--me);background:linear-gradient(90deg,rgba(180,83,9,.05),transparent 42%);
             border-radius:0 10px 10px 0;padding-top:8px;padding-bottom:8px}
  section.ai{border-color:var(--ai)}
  section h2{margin:0 0 10px;font-size:14px;font-weight:650;display:flex;align-items:center;gap:9px}
  section.me h2{color:var(--me)}
  section.ai h2{color:var(--ai)}
  section h2 .no{font-family:ui-monospace,Consolas,monospace;font-size:11.5px;
                 color:var(--tx3);background:var(--code-bg);border-radius:5px;padding:1px 7px}
  .body .md-h{color:inherit}
  section.ai .body .md-h{color:var(--ai)}   /* ★ 用户要求：AI 的小标题也换色 */
  section.me .body .md-h{color:var(--me)}
  .body h3{font-size:15.5px}

  footer{color:var(--tx3);font-size:12.5px;text-align:center;margin-top:50px;
         padding-top:22px;border-top:1px solid var(--line)}
  @media (max-width:640px){ .wrap{padding:0 12px 60px} section{padding-left:12px} }
</style>
</head>
<body>

<header class="top">
  <span class="t">完整对话记录 · 协同剪贴板</span>
  <span class="legend">
    <span class="m"><i></i>我的话</span>
    <span class="a"><i></i>AI 的话</span>
  </span>
</header>

<div class="wrap">

  <div class="intro">
    <h1>完整对话记录 · 协同剪贴板（本会话原文）</h1>

    <p><b>这份文件是什么</b>：本项目从动手到收尾的<b>完整对话原文</b> ——
    我发的每一句话、AI 给的每一段回复，<b>原样保留，不改写、不润色、不摘要</b>。
    （唯一的整理：连续几步工具调用归并到上一条 AI 回复下，内容一行未删。）</p>

    <p><b>来源</b>：DSH 会话原始日志 <span class="src">${esc(logPath.split(/[\\/]/).slice(-2).join('/'))}</span>
    （${frames} 个 zstd 帧拼接而成，共 ${recs.length} 条记录）。
    由 <code>dev/make-transcript.mjs</code> 逐条抽取，不是人工复述。</p>

    <p><b>颜色</b>：<span style="color:${C.me}">■ 我的话</span>　
    <span style="color:${C.ai}">■ AI 的话</span>　—— AI 回复里的小标题也换成蓝色。</p>

    <p><b>剔除的内容</b>：</p>
    <ul>
      <li>系统通知：权限变更 / 运行时上下文 / 技能目录 / 时间戳注入（${skipped.system} 条）</li>
      <li>AI 的内部思考块（reasoning，${skipped.reasoning} 个）</li>
      <li>工具调用的完整参数（只留一行"调用了什么"的摘要；连续几步已归并，原文 ${blocks.length} 块 → 归并后 ${merged.length} 块）</li>
      ${cutLines}
    </ul>

    <p>共 <b>${idx}</b> 个对话块（我 ${kept.filter((b) => b.who === 'me').length} / AI ${kept.filter((b) => b.who === 'ai').length}），
    归并前 ${merged.length} 块，去掉开头 ${drop.before} / 中段 ${drop.skip} / 末尾 ${drop.after}。</p>

    <p><b>分开存放</b>：另见《对话记录 02 · 题目讨论》（动手之前与 DeepSeek 的问答原文）
    与《我的开发经历》（对这段过程的回顾），三份性质不同，互不合并。</p>
  </div>

${sections}

  <footer>共 ${idx} 个对话块 · 由 dev/make-transcript.mjs 从会话原始日志自动抽取 · 不改写、不摘要</footer>
</div>

</body>
</html>
`;

fs.writeFileSync(outPath, html, 'utf8');
const size = fs.statSync(outPath).size;
console.log(`zstd 帧 ${frames} | 记录 ${recs.length} 条 | 对话块 ${blocks.length} → 归并 ${merged.length} → 保留 ${idx} | ${(size / 1024).toFixed(1)} KB`);
console.log(`去掉：开头 ${drop.before}、中段 ${drop.skip}、末尾 ${drop.after}`);
console.log(`剔除：系统通知 ${skipped.system}、空消息 ${skipped.empty}、思考块 ${skipped.reasoning}`);
console.log('->', outPath);
