/** store.js —— 消息的唯一数据源。
 *
 * 去重靠**房间内的 seq**（服务端给每条消息一个房间内单调递增的号）：
 *   · 同一条消息可能从两条路到达（POST 的响应 + SSE 推送）→ 只留一份；
 *   · SSE 断线重连后服务端会重发断线期间的消息 → 靠 seq 合并，不会重复。
 * 渲染层只认这个 store，不认"消息是从哪来的"。
 */

export class MessageStore {
  constructor() {
    this.items = [];
    this._seen = new Set();
  }

  get size() { return this.items.length; }
  get firstSeq() { return this.items.length ? this.items[0].seq : 0; }
  get lastSeq() { return this.items.length ? this.items[this.items.length - 1].seq : 0; }
  get lastAt() { return this.items.length ? this.items[this.items.length - 1].at : null; }

  clear() {
    this.items = [];
    this._seen.clear();
  }

  /** 追加（新消息）。按 seq 去重并排序，返回真正新增的那些。 */
  apply(messages) {
    const added = [];
    for (const msg of messages ?? []) {
      if (!msg || this._seen.has(msg.seq)) continue;
      this._seen.add(msg.seq);
      this.items.push(msg);
      added.push(msg);
    }
    if (added.length) this.items.sort((a, b) => a.seq - b.seq);
    return added;
  }

  /** 向前插入（翻页加载更早的消息） */
  prepend(messages) {
    const added = [];
    for (const msg of messages ?? []) {
      if (!msg || this._seen.has(msg.seq)) continue;
      this._seen.add(msg.seq);
      this.items.push(msg);
      added.push(msg);
    }
    if (added.length) this.items.sort((a, b) => a.seq - b.seq);
    return added;
  }

  all() {
    return this.items;
  }
}
