/**
 * dev/seed.mjs —— 往开发桩里灌几条示例数据，方便看界面效果
 *
 * 用 X-Forwarded-For 模拟局域网里的另外两台设备（桩服务优先读这个头），
 * 所以右侧"在线成员"能看到 3 个人各自用不同传输方式。
 *
 * 用法：node dev/seed.mjs
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const TMP = path.join(__dirname, 'tmp');
const BASE = process.env.BASE ?? 'http://127.0.0.1:8000';
const ROOM = process.env.ROOM ?? '1001';

const zhang = { clientId: 'seed-zhangming', nickname: '张明', ip: '192.168.1.9', transport: 'sse' };
const tablet = { clientId: 'seed-tablet', nickname: '平板', ip: '192.168.1.12', transport: 'polling' };
const me = { clientId: 'seed-me', nickname: '张明', ip: '192.168.1.7', transport: 'ws' };

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function post(person, payload) {
  const res = await fetch(`${BASE}/api/messages/${ROOM}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'x-forwarded-for': person.ip },
    body: JSON.stringify({ ...person, ...payload }),
  });
  if (!res.ok) throw new Error(`POST 失败 ${res.status} ${await res.text()}`);
  return (await res.json()).message;
}

const MIME = {
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
  '.gif': 'image/gif', '.webp': 'image/webp',
  '.py': 'text/x-python', '.txt': 'text/plain', '.md': 'text/markdown',
};

async function upload(person, filePath, name) {
  const ext = path.extname(name).toLowerCase();
  const fd = new FormData();
  fd.append('room', ROOM);
  fd.append('clientId', person.clientId);
  fd.append('file', new Blob([fs.readFileSync(filePath)], { type: MIME[ext] ?? 'application/octet-stream' }), name);
  const res = await fetch(`${BASE}/api/upload`, {
    method: 'POST',
    headers: { 'x-forwarded-for': person.ip },
    body: fd,
  });
  if (!res.ok) throw new Error(`上传失败 ${res.status} ${await res.text()}`);
  return (await res.json()).file;
}

await post(zhang, { type: 'text', source: 'text', text: '会议改到周三 14:00，会议室 B203。记得带上上周的流程图打印稿。' });
await sleep(150);

const shot = await upload(zhang, path.join(TMP, 'sample-shot.png'), '界面截图.png');
await post(zhang, { type: 'image', source: 'text', file: shot, text: shot.name });
await sleep(150);

const code = await upload(tablet, path.join(TMP, 'clipboard.py'), 'clipboard.py');
await post(tablet, { type: 'file', source: 'text', file: code, text: code.name });
await sleep(150);

await post(tablet, { type: 'text', source: 'clipboard', text: '先把后端两个接口跑通，再换 SSE。轮询版就别加连接状态了，没意义。' });
await sleep(150);

await post(me, { type: 'text', source: 'text', text: '图片收到了，很清楚。第三版布局我这边看着也同步上了。' });

console.log(`已往房间 ${ROOM} 灌入 5 条示例消息`);
