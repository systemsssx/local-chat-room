/**
 * api.js —— 所有 REST 调用的唯一出口。
 *
 * 三种传输的差别只在"下行怎么来"：
 *   · 轮询  ：下行靠定时 GET /api/snapshot
 *   · SSE   ：下行靠 EventSource 长连接
 *   · WS    ：下行靠 WebSocket
 * 而"上行发消息"这件事，轮询和 SSE 都必须走 POST，只有 WS 走 socket —— 代码里如实体现。
 */
import { clientId, nickname } from './session.js';

/** 服务端消息结构（三种传输拿到的都是这一个形状，Store 才能无差别合并）
 * {
 *   seq: 12,                        // 服务端单调递增，全局唯一，去重靠它
 *   id: 'uuid',
 *   room: '1001',
 *   clientId, nickname, ip,         // 谁发的
 *   type: 'text' | 'image' | 'file',
 *   text: '正文（type=text）',
 *   file: { url, name, size, mime, kind, lines, preview },  // type=image/file
 *   source: 'text' | 'clipboard',   // 由哪种输入模式产生（只做标记，不影响显示）
 *   transport: 'polling' | 'sse' | 'ws',  // 从哪条管道送出（只做标记）
 *   at: 1696578185123
 * }
 */

/**
 * 把失败响应翻成一句人能看的话。
 *
 * 服务端的错误体是 FastAPI 的 {"detail": "..."}，直接 throw 出整段 JSON 的话，
 * 界面上就会出现 `HTTP 400 {"detail":"只接受图片或纯文本文件"}` 这种给程序员看的东西。
 * 这里把 detail 抽出来，只留那句话；抽不到再退回原文。
 */
async function toJson(res) {
  if (!res.ok) {
    const raw = await res.text().catch(() => '');
    let detail = '';
    try {
      const parsed = JSON.parse(raw);
      detail = typeof parsed?.detail === 'string' ? parsed.detail
        : Array.isArray(parsed?.detail) ? parsed.detail.map((d) => d?.msg ?? '').join('；')
        : '';
    } catch { /* 不是 JSON，就用原文 */ }
    const message = detail || raw || `HTTP ${res.status}`;
    const err = new Error(message);
    err.status = res.status;
    throw err;
  }
  const type = res.headers.get('content-type') ?? '';
  if (!type.includes('json')) {
    throw new Error(`服务端返回的不是 JSON（${type || '无 content-type'}）`);
  }
  return res.json();
}

export const api = {
  /** 房间快照：初始加载 / 切换传输后补齐 / 轮询下行，三处共用同一个接口 */
  snapshot(room, since = 0, transport = '') {
    const q = new URLSearchParams({ since: String(since), clientId: clientId(), nickname: nickname() });
    if (transport) q.set('transport', transport);
    return fetch(`/api/snapshot/${encodeURIComponent(room)}?${q}`).then(toJson);
  },

  /** 发消息（轮询版与 SSE 版的上行通道） */
  send(room, payload) {
    return fetch(`/api/messages/${encodeURIComponent(room)}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ clientId: clientId(), nickname: nickname(), ...payload }),
    }).then(toJson);
  },

  /** 上传图片或纯文本文件，返回 file 描述对象（塞进消息里发出去） */
  upload(room, file) {
    const fd = new FormData();
    fd.append('room', room);
    fd.append('clientId', clientId());
    fd.append('file', file);
    return fetch('/api/upload', { method: 'POST', body: fd }).then(toJson);
  },

  /** 历史记录（SQLite）—— 右侧栏用，和窗口读的是同一份数据 */
  history(room, limit = 20) {
    return fetch(`/api/history/${encodeURIComponent(room)}?limit=${limit}`).then(toJson);
  },
};
