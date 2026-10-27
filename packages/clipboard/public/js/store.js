/**
 * store.js —— MessageStore：全应用**唯一**的数据源。
 *
 * 这是"六个板块共用一个信息窗口"的核心。规则只有三条：
 *   1. 三种传输（轮询 / SSE / WS）只负责把消息**灌进来**，它们不知道窗口长什么样；
 *   2. 两种模式（纯文字 / 剪贴板）只负责产出**待发送**的消息，它们不碰窗口；
 *   3. 窗口只订阅本 store —— 所以切换传输或模式时，窗口内容天然是连续的，一帧都不会清空。
 *
 * 合并策略：按服务端单调递增的 seq 去重。
 *   → 同一条消息无论从几条管道到达多少次（POST 响应 + SSE 推送 + WS 广播），窗口里只留一份。
 *   → 这让"切换管道"变成纯粹的换管子，不需要任何"清空 / 重建"逻辑。
 */
export class MessageStore {
  constructor() {
    /** @type {Map<number, object>} seq -> message */
    this._bySeq = new Map();
    /** @type {Array<object>} */
    this._members = [];
    this._subs = new Set();
    this.room = '';
    this._serverSeq = 0;
    this._status = { name: 'idle', text: '未连接' };
  }

  /* ---------- 读 ---------- */

  /** 本地已有的最大 seq：切换传输 / 重连时用它当 since，天然只补差额 */
  get lastSeq() {
    let max = 0;
    for (const seq of this._bySeq.keys()) if (seq > max) max = seq;
    return max;
  }

  get serverSeq() {
    return this._serverSeq;
  }

  get count() {
    return this._bySeq.size;
  }

  list() {
    return [...this._bySeq.values()].sort((a, b) => a.seq - b.seq);
  }

  get members() {
    return this._members;
  }

  get status() {
    return this._status;
  }

  /* ---------- 写 ---------- */

  /**
   * 合并一批消息（三种传输都调这一个方法，因此它们的行为完全一致）
   * @returns {number} 真正新增的条数，0 表示全是重复
   */
  apply(messages) {
    let added = 0;
    for (const m of messages ?? []) {
      if (!m || typeof m.seq !== 'number') continue;
      if (this._bySeq.has(m.seq)) continue;
      this._bySeq.set(m.seq, m);
      added++;
    }
    if (added > 0) this._emit({ type: 'messages', added });
    return added;
  }

  setMembers(list) {
    const next = Array.isArray(list) ? list : [];
    const same =
      next.length === this._members.length &&
      next.every((m, i) => m.clientId === this._members[i]?.clientId &&
                           m.transport === this._members[i]?.transport);
    if (same) return;
    this._members = next;
    this._emit({ type: 'members' });
  }

  setStatus(name, text, extra = {}) {
    this._status = { name, text, ...extra };
    this._emit({ type: 'status' });
  }

  setRoom(room) {
    this.room = room;
    this._emit({ type: 'room' });
  }

  setServerSeq(seq) {
    if (typeof seq === 'number' && seq > this._serverSeq) this._serverSeq = seq;
  }

  /** 只有换房间才需要清空（换传输、换模式都不清） */
  reset(room = '') {
    this._bySeq.clear();
    this._members = [];
    this._serverSeq = 0;
    this.room = room;
    this._emit({ type: 'reset' });
  }

  /* ---------- 订阅 ---------- */

  subscribe(fn) {
    this._subs.add(fn);
    return () => this._subs.delete(fn);
  }

  _emit(event) {
    for (const fn of this._subs) {
      try {
        fn(event, this);
      } catch (err) {
        console.error('[store] 订阅者报错：', err);
      }
    }
  }
}

export const store = new MessageStore();
