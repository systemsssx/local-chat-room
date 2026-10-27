/**
 * transports/index.js —— 传输注册表
 *
 * 三种传输对外是**同一个接口**：
 *   { name, label, path, hint, stats, start(), send(payload), stop() }
 * 所以"换传输"在 app.js 里就是三行：stop() 旧的 → 建新的 → start()。
 * 加第四种传输（比如长轮询）只需要在这里多注册一项，窗口和模式一行都不用改。
 */
import { createPollingTransport } from './polling.js';
import { createSseTransport } from './sse.js';
import { createWsTransport } from './ws.js';

export const TRANSPORTS = [
  { id: 'polling', label: 'HTTP 轮询', short: '轮询', en: 'POLLING',     create: createPollingTransport },
  { id: 'sse',     label: 'SSE 推送',  short: 'SSE',  en: 'EVENTSTREAM', create: createSseTransport },
  { id: 'ws',      label: 'WebSocket', short: 'WS',   en: 'DUPLEX',      create: createWsTransport },
];

export function transportMeta(id) {
  return TRANSPORTS.find((t) => t.id === id) ?? TRANSPORTS[0];
}
