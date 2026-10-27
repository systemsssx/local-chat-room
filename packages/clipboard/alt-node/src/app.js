/**
 * src/app.js —— 装配：把各层拼成一个可以 listen 的服务
 *
 * 顺序有讲究：
 *   1. /uploads 静态目录（上传的图片与文件）
 *   2. public 静态目录（前端页面）
 *   3. /api/* 接口
 *   4. WebSocket 挂在同一个 http.Server 的 upgrade 事件上 —— 所以 http 和 ws 共用 8000 端口
 */
import express from 'express';
import fs from 'node:fs';
import { createServer } from 'node:http';
import { config } from './config.js';
import { openDatabase } from './db.js';
import { roomStore } from './rooms.js';
import { mountNetRoutes } from './routes/net.js';
import { mountApiRoutes } from './routes/api.js';
import { mountSseRoute } from './routes/sse.js';
import { attachWebSocket } from './ws.js';

export function createApp() {
  fs.mkdirSync(config.uploadDir, { recursive: true });
  fs.mkdirSync(config.dataDir, { recursive: true });

  // 打开 SQLite（文件不存在会自动创建），并把消息序号接上历史最大值
  openDatabase();
  roomStore.init();

  const app = express();

  app.use(express.json({ limit: config.maxJsonBody }));
  app.use('/uploads', express.static(config.uploadDir, { maxAge: '1h', fallthrough: false }));
  app.use(express.static(config.publicDir));

  mountNetRoutes(app);      // /api/health、/api/net、/api/rooms/:room/connections
  mountApiRoutes(app);      // /api/snapshot、/api/messages、/api/history、/api/upload
  mountSseRoute(app);       // /api/sse/:room

  const server = createServer(app);
  const wss = attachWebSocket(server);   // 与 http 共用端口

  return { app, server, wss };
}
