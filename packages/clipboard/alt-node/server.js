/**
 * server.js —— 正式入口
 *
 * 启动：npm start        （默认 0.0.0.0:8000，局域网内可见）
 *
 * 启动横幅会把「手机该访问哪个地址」直接打出来 —— 这是局域网应用最容易卡住的一步：
 * 服务监听了 0.0.0.0 只代表"愿意接受局域网连接"，用户还得知道输哪个 IP。
 */
import { config, isLanOpen } from './src/config.js';
import { createApp } from './src/app.js';
import { accessInfo } from './src/net.js';
import { closeDatabase, db } from './src/db.js';

const { server, wss } = createApp();

function banner(info) {
  const line = '─'.repeat(62);
  const rows = [];
  rows.push('');
  rows.push('  协同剪贴板 · 服务已启动');
  rows.push(line);
  rows.push(`  本机访问    ${info.localUrl}`);

  if (!info.lanOpen) {
    rows.push('  局域网访问  ✗ 未开放');
    rows.push(`              （当前 HOST=${info.host}，只有本机能连；改成 HOST=0.0.0.0 即可对局域网开放）`);
  } else if (!info.addresses.length) {
    rows.push('  局域网访问  ✗ 没找到可用的 IPv4 地址（网卡没连上？）');
  } else {
    const real = info.addresses.filter((a) => !a.virtual);
    const virt = info.addresses.filter((a) => a.virtual);
    rows.push('  局域网访问  ↓ 手机/平板连同一个 WiFi，浏览器直接输下面的地址');
    for (const a of (real.length ? real : info.addresses)) {
      rows.push(`              ${a.url}     [${a.name} · ${a.cidr}]`);
    }
    for (const a of virt) {
      rows.push(`              ${a.url}     [${a.name} · 虚拟网卡，多半连不上]`);
    }
  }

  rows.push(line);
  rows.push(`  监听        ${info.host}:${info.port}${isLanOpen() ? '（局域网内可见）' : '（仅本机）'}`);
  rows.push(`  上传上限    ${Math.round(config.maxUploadBytes / 1024 / 1024)} MB · 图片与纯文本文件（自动识别 UTF-8 / GBK）`);
  rows.push(`  房间数据    SQLite · data/app.db（重启不丢，已存 ${db.stats().messages} 条消息 / ${db.stats().rooms} 个房间）`);
  rows.push(line);
  rows.push('  按 Ctrl+C 停止');
  rows.push('');
  console.log(rows.join('\n'));
}

server.listen(config.port, config.host, () => {
  banner(accessInfo({ host: config.host, port: config.port }));
});

server.on('error', (err) => {
  if (err.code === 'EADDRINUSE') {
    console.error(`\n✗ 端口 ${config.port} 已被占用。换一个端口：PORT=8001 npm start\n`);
  } else {
    console.error('\n✗ 启动失败：', err.message, '\n');
  }
  process.exit(1);
});

/* ---------- 优雅关闭：先停接收新连接，再断开长连接，最后关服务 ---------- */
let closing = false;
async function shutdown(signal) {
  if (closing) return;
  closing = true;
  console.log(`\n收到 ${signal}，正在关闭…`);

  for (const ws of wss.clients) {
    try { ws.close(1001, 'server shutting down'); } catch { /* ignore */ }
  }
  wss.close();

  server.close(() => {
    closeDatabase();
    console.log('已关闭（数据库已安全断开）。');
    process.exit(0);
  });
  // 兜底：2 秒内没关干净就强退（避免 SSE 长连接拖着不退）
  setTimeout(() => { closeDatabase(); process.exit(0); }, 2000).unref();
}

for (const sig of ['SIGINT', 'SIGTERM']) {
  process.on(sig, () => shutdown(sig));
}
