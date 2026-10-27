"""端到端验证：六个组合是不是真的共用一个信息窗口。

用独立的房间 #room=e2e（从 0 条开始），依次做：
  1) 用 SSE 发一条 → 计数 1
  2) 切到轮询 → 窗口必须还是 1 条（没有被清空）
  3) 用轮询发一条 → 计数 2
  4) 切到 WebSocket → 窗口必须还是 2 条
  5) 用 WS 发一条 → 计数 3
  6) 切换输入模式 → 输入框应消失/出现
  7) 检查每条消息的管道标记 = {SSE, POLL, WS}
最后截图到 shots/。
"""
import pathlib
import random
import re
import sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
# 每次跑用一个全新房间，保证断言是绝对计数（内存桩重启即清空，所以不能复用房间名）
ROOM = "e2e" + str(random.randint(10000, 99999))
OUT = pathlib.Path(r"C:\work\collab-clipboard\design\shots")
OUT.mkdir(parents=True, exist_ok=True)
errors: list[str] = []
fails: list[str] = []


def check(name, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""))
    if not ok:
        fails.append(name)


def count(page) -> int:
    return page.locator(".msg").count()


with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome")
    ctx = browser.new_context(
        viewport={"width": 1600, "height": 1000},
        device_scale_factor=2,
        permissions=["clipboard-read", "clipboard-write"],
    )
    page = ctx.new_page()
    page.on("console", lambda m: errors.append(f"{m.type}: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"PAGEERROR: {e}"))

    page.goto(f"{BASE}/#room={ROOM}", wait_until="load")
    page.wait_for_timeout(2000)

    print(f"\n（本次使用房间 {ROOM}）")
    print("\n— 初始状态 —")
    check("页面无 JS 报错", not errors, "; ".join(errors[:3]))
    check("初始房间为空", count(page) == 0, f"实际 {count(page)} 条")
    check("默认走 SSE", "SSE" in page.locator("#comboA").inner_text(), page.locator("#comboA").inner_text())

    print("\n— ① 用 SSE 通道发一条 —")
    page.fill("input.comp", "第一条：从 SSE 通道发出")
    page.click("button.send")
    page.wait_for_timeout(1200)
    check("窗口出现 1 条", count(page) == 1, f"实际 {count(page)}")

    print("\n— ② 切到 HTTP 轮询：窗口不能清空 —")
    page.locator(".chan").nth(0).click()
    page.wait_for_timeout(2500)
    check("切换后仍是 1 条（未被清空）", count(page) == 1, f"实际 {count(page)}")
    check("顶栏组合显示已切到 CH1", "轮询" in page.locator("#comboA").inner_text(), page.locator("#comboA").inner_text())

    print("\n— ③ 用轮询通道发一条 —")
    page.fill("input.comp", "第二条：从轮询通道发出")
    page.click("button.send")
    page.wait_for_timeout(2200)
    check("窗口累计 2 条", count(page) == 2, f"实际 {count(page)}")

    print("\n— ④ 切到 WebSocket：窗口仍不能清空 —")
    page.locator(".chan").nth(2).click()
    page.wait_for_timeout(2000)
    check("切换后仍是 2 条", count(page) == 2, f"实际 {count(page)}")
    check("顶栏组合显示已切到 CH3", "WebSocket" in page.locator("#comboA").inner_text(), page.locator("#comboA").inner_text())

    print("\n— ⑤ 用 WebSocket 通道发一条 —")
    page.fill("input.comp", "第三条：从 WebSocket 通道发出")
    page.click("button.send")
    page.wait_for_timeout(1800)
    check("窗口累计 3 条", count(page) == 3, f"实际 {count(page)}")

    print("\n— ⑥ 切换输入模式：只换底栏 —")
    page.click("#modeSeg button[data-mode='clipboard']")
    page.wait_for_timeout(400)
    check("剪贴板模式：输入框消失", page.locator("input.comp").count() == 0)
    check("剪贴板模式：出现监听状态条", page.locator(".listen").count() == 1)
    check("剪贴板模式：窗口仍是 3 条", count(page) == 3, f"实际 {count(page)}")
    page.click("#modeSeg button[data-mode='text']")
    page.wait_for_timeout(300)
    check("切回纯文字：输入框回来", page.locator("input.comp").count() == 1)
    check("切回纯文字：窗口仍是 3 条", count(page) == 3, f"实际 {count(page)}")

    print("\n— ⑦ 三条消息分别由哪条管道送出 —")
    tags = [t.strip() for t in page.locator(".m-tags").all_inner_texts()]
    flat = [x for t in tags for x in re.split(r"\s+", t) if x]
    check("管道标记覆盖 SSE / POLL / WS", {"SSE", "POLL", "WS"}.issubset(set(flat)), str(flat))

    print("\n— ⑧ 右侧面板 —")
    check("在线成员已填充", page.locator("#members .mem").count() >= 1, page.locator("#members .mem").count())
    check("历史记录已填充", page.locator("#historyList .hi").count() >= 1, page.locator("#historyList .hi").count())
    check("消息计数同步", "3 条" in page.locator("#streamCount").inner_text(), page.locator("#streamCount").inner_text())
    upd = page.locator("#streamUpdated").inner_text()
    check("显示最近一次更新时间（任务一要求6）", "最近更新" in upd and "—" not in upd, upd)

    page.wait_for_timeout(500)
    page.screenshot(path=str(OUT / "v5-app-e2e.png"))
    print(f"\n截图 -> {OUT / 'v5-app-e2e.png'}")

    # 顺手给主房间 1001 也截一张（有示例数据，用来展示）
    # 注意：只改 hash 浏览器不会重新加载，必须显式 reload
    page.goto(f"{BASE}/#room=1001", wait_until="load")
    page.reload(wait_until="load")
    page.wait_for_timeout(2500)
    svc = page.locator("#serviceInfo").inner_text().replace("\n", " | ")
    print(f"  房间/服务面板 -> {svc}")
    check("服务面板消息总数与窗口一致", f"{count(page)}" in svc, svc)
    page.screenshot(path=str(OUT / "v5-app-room1001.png"))
    print(f"截图 -> {OUT / 'v5-app-room1001.png'}")

    print("\n— 控制台错误 —")
    print("  无" if not errors else "\n".join("  " + e for e in errors[:10]))
    browser.close()

print("\n结果：" + ("全部通过" if not fails else f"{len(fails)} 项失败 -> {fails}"))
sys.exit(1 if fails else 0)
