/**
 * dev/db-check.mjs —— 验证 SQLite 持久化
 *
 *   node dev/db-check.mjs write    往测试房间写两条，然后读出来
 *   node dev/db-check.mjs read     只读（重启后再跑一次，证明数据没丢）
 *
 * 环境变量：BASE（默认 http://127.0.0.1:8000）、ROOM（默认 dbtest）
 */
const BASE = process.env.BASE ?? 'http://127.0.0.1:8000';
const ROOM = process.env.ROOM ?? 'dbtest';
const mode = process.argv[2] ?? 'read';

async function post(text) {
  const res = await fetch(`${BASE}/api/messages/${encodeURIComponent(ROOM)}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      clientId: 'db-check', nickname: '建库测试', transport: 'polling',
      type: 'text', source: 'text', text,
    }),
  });
  if (!res.ok) throw new Error(`POST 失败 ${res.status} ${await res.text()}`);
  return (await res.json()).message;
}

async function read(tag) {
  const history = await fetch(`${BASE}/api/history/${encodeURIComponent(ROOM)}?limit=20`).then((r) => r.json());
  const snap = await fetch(`${BASE}/api/snapshot/${encodeURIComponent(ROOM)}?since=0`).then((r) => r.json());
  const health = await fetch(`${BASE}/api/health`).then((r) => r.json());

  console.log(`\n[${tag}]`);
  console.log(`  房间 ${ROOM}：历史 ${history.items.length} 条 | 快照带回 ${snap.messages.length} 条 | 服务端 seq = ${snap.seq}`);
  for (const it of history.items.slice(0, 3)) {
    console.log(`    #${String(it.seq).padStart(3)}  ${new Date(it.at).toLocaleTimeString('zh-CN', { hour12: false })}  ${it.label}`);
  }
  console.log(`  数据库：${health.db.file} · ${health.db.messages} 条消息 / ${health.db.rooms} 个房间 · ${health.db.sizeKB} KB`);
  return { count: history.items.length, seq: snap.seq, messages: snap.messages.length };
}

if (mode === 'write') {
  const a = await post('持久化测试 A：这条在服务重启后应该还在');
  const b = await post('持久化测试 B：图片与文件只存 URL，二进制不入库');
  console.log(`已写入 #${a.seq} 和 #${b.seq}`);
  await read('写入后');
} else {
  await read('重启后（数据应该还在）');
}
