/**
 * panels.js —— 左栏（三种方案）/ 右栏（成员·服务·历史）的渲染函数
 *
 * 全是"给数据就重画"的纯函数，不持有状态：
 * 谁调它们、什么时候调，由 app.js 决定。这样切换传输时只需要重画左栏，
 * 信息窗口和右栏完全不受影响。
 */
import { el, fmtTime, fmtSize } from './ui.js';

/** 左栏：三个方案。电平梯只给"当前选中的那条"点亮，其余熄灭（同时只跑一条管道，这是事实） */
export function renderChannels(container, { transports, activeId, levels, onPick }) {
  container.replaceChildren(
    ...transports.map((t, i) => {
      const active = t.id === activeId;
      const lit = active ? Math.max(1, Math.min(5, levels?.[t.id] ?? 2)) : 0;
      return el('div', {
        class: 'chan' + (active ? ' active' : ''),
        onclick: () => onPick(t.id),
        title: active ? '当前使用的通信方式' : `切换到 ${t.label}`,
      }, [
        el('span', { class: 'num', text: String(i + 1).padStart(2, '0') }),
        el('div', { class: 'chan-txt' }, [
          el('div', { class: 'nm', text: t.label }),        // 桌面版用全名
          el('div', { class: 'nm-s', text: t.short ?? t.label }),  // 手机版用简称（CSS 决定显示哪个）
          el('div', { class: 'en', text: t.en }),
        ]),
        el('div', { class: 'meter' + (lit ? ' lv' + lit : '') }, [0, 1, 2, 3, 4].map(() => el('i'))),
        active ? el('i', { class: 'led' }) : null,
      ]);
    })
  );
}

/** 右栏：在线成员。信号格按"最近活跃度"给：WS/SSE 长连接满格，轮询一格 */
export function renderMembers(container, members, myClientId) {
  if (!members?.length) {
    container.replaceChildren(el('div', { class: 'kv', text: '暂时只有你自己' }));
    return;
  }
  container.replaceChildren(
    ...members.map((m) => {
      const mine = m.clientId === myClientId;
      const lvl = m.transport === 'ws' ? 3 : m.transport === 'sse' ? 3 : 1;
      return el('div', { class: 'mem', title: `最近活跃 ${fmtTime(m.at)}` }, [
        el('i', { class: 'led' + (m.stale ? ' w' : '') }),
        el('div', { class: 'mem-txt' }, [
          el('div', { class: 'nm', text: mine ? `${m.nickname} · 本机` : m.nickname }),
          el('div', { class: 'ip', text: m.ip ?? '' }),
        ]),
        el('div', { class: 'sig s' + lvl }, [el('i'), el('i'), el('i')]),
        el('span', { class: 'chip' + (m.transport === 'polling' ? ' dim' : ''), text: (m.transport ?? '?').toUpperCase() }),
      ]);
    })
  );
}

/** 右栏：房间 / 服务信息 */
export function renderService(container, info) {
  const rows = [
    ['房间号', info.room],
    ['消息总数', String(info.total)],
    ['服务地址', info.addr],
    ['已运行', info.uptime],
    ['数据库', info.db],
  ];
  container.replaceChildren(
    ...rows.map(([k, v]) => el('div', { class: 'kv2' }, [el('span', { text: k }), el('b', { text: v ?? '—' })]))
  );
}

/** 右栏：历史记录（SQLite）。恢复＝把那条内容重新发一条消息，全局广播 */
export function renderHistory(container, items, onRestore) {
  if (!items?.length) {
    container.replaceChildren(el('div', { class: 'kv', text: '还没有历史' }));
    return;
  }
  container.replaceChildren(
    ...items.map((it) =>
      el('div', { class: 'hi' }, [
        el('span', { class: 'ht', text: fmtTime(it.at) }),
        el('span', { class: 'hs', text: it.label, title: it.label }),
        el('button', { class: 'mini', onclick: () => onRestore(it), title: '把这条内容重新发到房间' }, ['恢复']),
      ])
    )
  );
}

/** 顶栏右侧电平条：16 段，按最近 3 秒的消息量点亮 */
export function renderVu(container, numEl, recentCount) {
  const lit = Math.max(1, Math.min(16, recentCount * 3 + 1));
  const bars = container.children;
  for (let i = 0; i < bars.length; i++) bars[i].classList.toggle('on', i < lit);
  if (numEl) numEl.textContent = `${recentCount.toFixed(1)} msg/s`;
}

export { fmtSize };
