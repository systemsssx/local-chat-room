/**
 * src/routes/net.js —— 局域网相关的接口
 *
 * 前端靠 /api/net 知道"手机该访问哪个地址"，从而把它显示出来（右栏"服务地址"、底栏提示）。
 * 服务自己只监听 0.0.0.0 是不够的：用户得知道要输哪个 IP。
 */
import { accessInfo } from '../net.js';
import { config, isLanOpen } from '../config.js';
import { roomStore, hub, presence } from '../rooms.js';
import { db } from '../db.js';

export function mountNetRoutes(app) {
  /** 健康检查：给运维/脚本用，也方便确认服务活着 */
  app.get('/api/health', (req, res) => {
    res.json({
      ok: true,
      pid: process.pid,
      uptimeSec: Math.round(process.uptime()),
      msgs: roomStore.seq,
      memoryMB: Math.round(process.memoryUsage().rss / 1048576),
      db: db.stats(),                    // SQLite 文件、消息数、房间数、大小
    });
  });

  /** 网络信息：本机地址、局域网地址、是否对外开放 */
  app.get('/api/net', (req, res) => {
    const info = accessInfo({ host: config.host, port: config.port });
    res.json({
      ...info,
      warning: info.lanOpen
        ? (info.primaryUrl ? null : '没有找到可用的局域网 IPv4 地址（网卡没连上？）')
        : '当前只监听本机（HOST=' + config.host + '），局域网内其它设备连不上',
    });
  });

  /** 某个房间的连接实况（截图/验收时用得上） */
  app.get('/api/rooms/:room/connections', (req, res) => {
    const { room } = req.params;
    res.json({
      room,
      sse: hub.sseCount(room),
      ws: hub.wsCount(room),
      members: presence.list(room).length,
      messages: roomStore.since(room, 0).length,
    });
  });
}
