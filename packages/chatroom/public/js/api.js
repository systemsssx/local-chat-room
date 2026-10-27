/** api.js —— 所有 REST 调用的唯一出口。
 *
 * 两条约定：
 *  1. **相对路径**：不写死 localhost —— 手机用局域网 IP 打开、将来上公网都靠这一条；
 *  2. **401 统一处理**：任何接口返回 401 就说明票没了（过期 / 被踢 / 登出），
 *     立刻回调上层弹回登录页，而不是让界面白屏或吐一句生硬的报错。
 */

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

let onUnauthorized = null;
export function setUnauthorizedHandler(fn) {
  onUnauthorized = fn;
}

async function request(path, { method = 'GET', json, form } = {}) {
  const init = { method, credentials: 'same-origin', headers: {} };
  if (json !== undefined) {
    init.headers['content-type'] = 'application/json';
    init.body = JSON.stringify(json);
  }
  if (form) init.body = form;

  let res;
  try {
    res = await fetch(path, init);
  } catch (err) {
    throw new ApiError('连不上服务端，检查一下服务是否还在跑', 0);
  }

  if (res.status === 401) {
    onUnauthorized?.();
    throw new ApiError('登录已过期，请重新登录', 401);
  }
  if (!res.ok) {
    let detail = '';
    try {
      const data = await res.json();
      if (typeof data?.detail === 'string') detail = data.detail;
      else if (Array.isArray(data?.detail)) detail = data.detail.map((d) => d?.msg ?? '').join('；');
    } catch { /* 不是 JSON 就用状态码 */ }
    throw new ApiError(detail || `HTTP ${res.status}`, res.status);
  }
  if (res.status === 204) return null;
  const type = res.headers.get('content-type') ?? '';
  if (!type.includes('json')) return null;
  return res.json();
}

export const api = {
  // 认证
  me: () => request('/api/auth/me'),
  register: (username, password) =>
    request('/api/auth/register', { method: 'POST', json: { username, password } }),
  login: (username, password) =>
    request('/api/auth/login', { method: 'POST', json: { username, password } }),
  logout: () => request('/api/auth/logout', { method: 'POST' }),

  // 房间
  rooms: () => request('/api/rooms'),
  createRoom: (name, visibility) => request('/api/rooms', { method: 'POST', json: { name, visibility } }),
  joinRoom: (code) => request('/api/rooms/join', { method: 'POST', json: { code } }),
  leaveRoom: (roomId) => request(`/api/rooms/${roomId}/leave`, { method: 'POST' }),
  resetCode: (roomId) => request(`/api/rooms/${roomId}/code`, { method: 'POST' }),

  // 消息
  snapshot: (roomId, since = 0) => request(`/api/snapshot/${roomId}?since=${since}`),
  send: (roomId, payload) => request(`/api/messages/${roomId}`, { method: 'POST', json: payload }),
  history: (roomId, before = 0, limit = 30) =>
    request(`/api/history/${roomId}?before=${before}&limit=${limit}`),
  upload: (roomId, file) => {
    const form = new FormData();
    form.append('roomId', String(roomId));
    form.append('file', file);
    return request('/api/upload', { method: 'POST', form });
  },

  // 服务信息
  net: () => request('/api/net'),
  health: () => request('/api/health'),
};
