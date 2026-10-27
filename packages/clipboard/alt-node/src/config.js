/**
 * src/config.js —— 所有配置集中在这里，改一处全局生效
 *
 * 优先级：环境变量 > 默认值。启动方式：
 *   npm start                     默认 0.0.0.0:8000（局域网可见）
 *   PORT=9000 npm start           换端口
 *   HOST=127.0.0.1 npm start      只监听本机（局域网连不上，仅调试用）
 */
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

export const ROOT_DIR = ROOT;

export const config = {
  /** 监听地址：0.0.0.0 = 所有网卡（局域网内其它设备能连）；127.0.0.1 = 只有本机 */
  host: process.env.HOST ?? '0.0.0.0',
  port: Number(process.env.PORT ?? 8000),

  /** 目录 */
  publicDir: path.join(ROOT, 'public'),
  uploadDir: path.join(ROOT, 'uploads'),
  dataDir: path.join(ROOT, 'data'),

  /** 限制（题目要求"对大小和类型进行合理限制"） */
  maxUploadBytes: 2 * 1024 * 1024,      // 单个上传文件上限 2MB
  maxTextBytes: 64 * 1024,              // 单条文字上限 64KB
  maxJsonBody: '1mb',

  /** 连接与心跳 */
  ssePingMs: 20_000,                    // SSE 心跳间隔
  wsPingMs: 30_000,                     // WebSocket 心跳间隔
  memberTtlMs: 15_000,                  // 超过这么久没动静就不算"在线"（轮询客户端靠这个判断）
  wsReconnectMaxMs: 10_000,             // 客户端重连退避上限（前端用）

  /** 历史记录 */
  historyLimit: 20,                     // 题目要求"最近 20 条"
};

/** 是否对局域网开放（决定启动横幅和 /api/net 的提示） */
export const isLanOpen = () => config.host === '0.0.0.0' || config.host === '::';
