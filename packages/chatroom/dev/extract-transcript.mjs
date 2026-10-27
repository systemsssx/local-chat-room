/**
 * dev/extract-transcript.mjs —— 从 DSH 会话原始日志里抽出完整对话记录
 *
 * 背景：会话日志是 `session.v4.jsonl.zstd`，**多个 zstd 帧拼接**而成
 * （写入端每 flush 一次追加一帧），Node 的 zstdDecompressSync / 流式接口都只解第一帧，
 * 所以这里自己走一遍 zstd 帧结构，切出每一帧再分别解压。
 *
 * 用法：
 *   node dev/extract-transcript.mjs probe  <日志路径>                  只探查结构
 *   node dev/extract-transcript.mjs dump   <日志路径> <输出md路径>      生成对话记录
 */
import fs from 'node:fs';
import zlib from 'node:zlib';

/* ---------- 1. 多帧 zstd 解压 ---------- */
const MAGIC = 0xfd2fb528;        // zstd 帧魔数（小端读出）
const SKIP_BASE = 0x184d2a50;    // 可跳过帧魔数基址

function frameEnd(buf, start) {
  let p = start;
  const magic = buf.readUInt32LE(p);
  if ((magic & 0xfffffff0) === SKIP_BASE) {
    return p + 8 + buf.readUInt32LE(p + 4);          // 可跳过帧：魔数4 + 长度4 + 数据
  }
  if (magic !== MAGIC) return -1;
  p += 4;
  const fhd = buf[p++];
  const fcsFlag = fhd >> 6;
  const singleSegment = (fhd >> 5) & 1;
  const dictFlag = fhd & 3;
  const hasChecksum = (fhd >> 2) & 1;
  if (!singleSegment) p += 1;                        // Window_Descriptor
  p += [0, 1, 2, 4][dictFlag];                       // Dictionary_ID
  const fcsSize = fcsFlag === 0 ? (singleSegment ? 1 : 0) : [0, 2, 4, 8][fcsFlag];
  p += fcsSize;                                      // Frame_Content_Size
  for (;;) {                                         // 逐块走到 Last_Block
    const h = buf[p] | (buf[p + 1] << 8) | (buf[p + 2] << 16);
    const last = h & 1;
    const btype = (h >> 1) & 3;
    const bsize = h >> 3;
    p += 3;
    if (btype === 0) p += bsize;                     // Raw
    else if (btype === 1) p += 1;                    // RLE
    else if (btype === 2) p += bsize;                // Compressed
    else throw new Error('保留块类型，解析失败');
    if (last) break;
  }
  if (hasChecksum) p += 4;
  return p;
}

function decompressAll(file) {
  const buf = fs.readFileSync(file);
  const parts = [];
  let p = 0;
  let frames = 0;
  while (p < buf.length) {
    const end = frameEnd(buf, p);
    if (end < 0 || end > buf.length) throw new Error(`第 ${frames} 帧边界异常：p=${p} end=${end}`);
    parts.push(zlib.zstdDecompressSync(buf.subarray(p, end)));
    p = end;
    frames++;
  }
  return { text: Buffer.concat(parts).toString('utf8'), frames };
}

/* ---------- 2. 取文本 ---------- */
/** 把一条记录里的可见文本抠出来（不同版本字段位置不同，逐个兜底） */
function textsOf(rec) {
  const out = [];
  const push = (v) => { if (typeof v === 'string' && v.trim()) out.push(v); };
  const m = rec.message ?? rec.msg ?? rec;
  if (typeof m === 'string') push(m);
  if (Array.isArray(m?.content)) {
    for (const c of m.content) {
      if (typeof c === 'string') push(c);
      else if (c?.type === 'text' || c?.type === 'input_text' || c?.type === 'output_text') push(c.text);
    }
  } else if (typeof m?.content === 'string') push(m.content);
  push(m?.text);
  push(rec.text);
  return out;
}

const roleOf = (rec) =>
  rec.role ?? rec.message?.role ?? rec.msg?.role ?? rec.author ?? rec.sender ?? null;

/* ---------- 3. 主流程 ---------- */
const [mode, logPath, outPath] = process.argv.slice(2);
if (!mode || !logPath) {
  console.error('用法: node dev/extract-transcript.mjs <probe|dump> <日志路径> [输出md]');
  process.exit(1);
}

const { text, frames } = decompressAll(logPath);
const lines = text.split('\n').filter(Boolean);
console.log(`zstd 帧数: ${frames}   解压后: ${text.length} 字节   JSONL 行数: ${lines.length}`);

const recs = [];
for (const l of lines) { try { recs.push(JSON.parse(l)); } catch { /* 跳过坏行 */ } }

const kinds = {};
for (const r of recs) kinds[r.type ?? '?'] = (kinds[r.type ?? '?'] ?? 0) + 1;
console.log('类型分布:', JSON.stringify(kinds));

const roles = {};
for (const r of recs) { const k = `${r.type}/${roleOf(r)}`; roles[k] = (roles[k] ?? 0) + 1; }
console.log('类型+角色:', JSON.stringify(roles));

if (mode === 'probe') {
  const seen = new Set();
  for (const r of recs) {
    const key = `${r.type}/${roleOf(r)}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const ts = textsOf(r);
    console.log(`\n--- 样本 ${key} | 字段: ${Object.keys(r).join(',')}`);
    console.log(`    可见文本 ${ts.length} 段，第一段前 200 字: ${(ts[0] ?? '(无)').slice(0, 200).replace(/\n/g, ' ⏎ ')}`);
  }
  process.exit(0);
}

/* 抽成 markdown 对话记录 */
const NAMES = { user: '我', human: '我', assistant: 'AI', model: 'AI', system: '系统', tool: '工具' };
const out = [];
let n = 0;
for (const r of recs) {
  const role = roleOf(r);
  if (!role || !NAMES[role]) continue;
  if (r.type && !['message', 'user_message', 'assistant_message', 'turn', 'text'].includes(r.type)) continue;
  const ts = textsOf(r);
  if (!ts.length) continue;
  n++;
  out.push(`## ${n}. ${NAMES[role]}\n`);
  for (const t of ts) out.push(t.trim() + '\n');
  out.push('---\n');
}

fs.writeFileSync(outPath, out.join('\n'), 'utf8');
console.log(`\n写出 ${n} 条消息 -> ${outPath}`);
