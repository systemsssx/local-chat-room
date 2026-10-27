/**
 * dev/db-inspect.mjs —— 绕过服务，直接打开 SQLite 文件看里面存了什么
 *
 * 用法：node dev/db-inspect.mjs [库文件路径]
 */
import { DatabaseSync } from 'node:sqlite';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const file = process.argv[2] ?? path.join(ROOT, 'data', 'app.db');

const db = new DatabaseSync(file);

console.log(`库文件：${file}`);
console.log('\n表：');
for (const r of db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").all()) {
  console.log('  ·', r.name);
}

console.log('\nrooms（房间当前状态表）：');
const rooms = db.prepare('SELECT * FROM rooms ORDER BY updated_at DESC').all();
if (!rooms.length) console.log('  (空)');
for (const r of rooms) {
  console.log(`  房间 ${r.room}  seq=${r.seq}  更新于 ${new Date(r.updated_at).toLocaleString('zh-CN')}`);
  console.log(`    当前内容：${String(r.text).slice(0, 60)}`);
  console.log(`    最后修改者：${r.updated_by}`);
}

console.log('\nmessages（历史记录表）：');
const rows = db.prepare('SELECT * FROM messages ORDER BY seq ASC').all();
if (!rows.length) console.log('  (空)');
for (const m of rows) {
  const kind = m.file_json ? `${m.type}(${JSON.parse(m.file_json).name})` : m.type;
  console.log(`  #${String(m.seq).padStart(3)}  [${m.room}]  ${new Date(m.at).toLocaleTimeString('zh-CN', { hour12: false })}  ${kind}  由 ${m.nickname}(${m.ip}) 经 ${m.transport} 送出`);
  if (m.text) console.log(`        "${String(m.text).slice(0, 70)}"`);
}

console.log(`\n合计：${rows.length} 条消息 / ${rooms.length} 个房间`);
db.close();
