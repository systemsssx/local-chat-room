"""验证"新消息自动滑动到底部"的行为（含那个真 bug 的回归）。

要验的三件事：
  1. 首次载入历史 → 自动在底部（而不是停在顶部）
  2. 你翻上去看历史时来新消息 → **不硬把你拽到底部**，而是浮出「N 条新消息 ↓」
  3. 点那个按钮 → 滑到底部、按钮消失

用手机版视口跑（消息条数足够撑出滚动条）。
"""
import pathlib
import sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
OUT = pathlib.Path(r"C:\work\collab-clipboard\design\shots")
fails: list[str] = []
errors: list[str] = []


def check(name, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""))
    if not ok:
        fails.append(name)


METRICS = """(() => {
  const el = document.getElementById('msgs');
  return {
    scrollTop: Math.round(el.scrollTop),
    scrollHeight: el.scrollHeight,
    clientHeight: el.clientHeight,
    atBottom: el.scrollHeight - el.scrollTop - el.clientHeight <= 2,
  };
})()"""

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome")
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2,
                              is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.on("console", lambda m: errors.append(f"{m.type}: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"PAGEERROR: {e}"))

    page.goto(f"{BASE}/mobile.html#room=1001", wait_until="load")
    page.wait_for_timeout(2500)

    print("\n— ① 首次载入：应该自动停在底部 —")
    m = page.evaluate(METRICS)
    check("消息足够多能滚动", m["scrollHeight"] > m["clientHeight"], m)
    check("初始就在底部", m["atBottom"], m)
    check("没有多余的「新消息」按钮", page.locator(".jump-bottom.show").count() == 0)

    print("\n— ② 翻上去看历史，来一条新消息 —")
    page.evaluate("document.getElementById('msgs').scrollTop = 0")
    page.wait_for_timeout(400)
    check("已滚到顶部", page.evaluate(METRICS)["scrollTop"] < 5)

    # 用另一个"人"发一条（模拟手机/别人的消息进来）
    page.evaluate("""async () => {
      await fetch('/api/messages/1001', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ clientId: 'scroll-test', nickname: '滚动测试', transport: 'sse',
                               type: 'text', source: 'text', text: '这条不该把你拽到底部' }),
      });
    }""")
    page.wait_for_timeout(1500)

    m2 = page.evaluate(METRICS)
    check("没有被硬拽到底部", m2["scrollTop"] < 5, m2)
    check("浮出了「新消息 ↓」按钮", page.locator(".jump-bottom.show").count() == 1)
    label = page.locator(".jump-bottom").inner_text()
    check("按钮文案带条数", "1 条新消息" in label, label)
    page.screenshot(path=str(OUT / "m6-mobile-newmsg-hint.png"))
    print(f"  截图 -> {OUT / 'm6-mobile-newmsg-hint.png'}")

    print("\n— ③ 点按钮 → 滑到底部、按钮消失 —")
    page.locator(".jump-bottom").click()
    page.wait_for_timeout(700)
    m3 = page.evaluate(METRICS)
    check("已经到底部", m3["atBottom"], m3)
    check("按钮收起", page.locator(".jump-bottom.show").count() == 0)

    print("\n— ④ 在底部时来新消息：应该自动跟着走 —")
    before = page.evaluate(METRICS)["scrollTop"]
    page.evaluate("""async () => {
      await fetch('/api/messages/1001', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ clientId: 'scroll-test', nickname: '滚动测试', transport: 'sse',
                               type: 'text', source: 'text', text: '这条应该自动滚出来' }),
      });
    }""")
    page.wait_for_timeout(1500)
    m4 = page.evaluate(METRICS)
    check("自动跟到底部", m4["atBottom"] and m4["scrollTop"] > before, f"{before} -> {m4['scrollTop']}")
    check("新消息确实在视口里", "这条应该自动滚出来" in page.locator("#msgs").inner_text())
    check("没有出现「新消息」按钮", page.locator(".jump-bottom.show").count() == 0)

    print("\n— ⑤ 自己发消息：即便先翻在上面，也必须滑到底部 —")
    page.evaluate("document.getElementById('msgs').scrollTop = 0")
    page.wait_for_timeout(400)
    check("已滚到顶部", page.evaluate(METRICS)["scrollTop"] < 5)
    page.fill("input.comp", "我自己发的，应该一定看得到")
    page.click("button.send")
    page.wait_for_timeout(1600)
    m5 = page.evaluate(METRICS)
    check("自己发消息后滑到底部", m5["atBottom"], m5)
    check("新消息在视口里", "我自己发的，应该一定看得到" in page.locator("#msgs").inner_text())
    check("没留下「新消息」按钮", page.locator(".jump-bottom.show").count() == 0)

    print("\n— ⑥ 桌面版也要一样（不再给不能滚动的元素设 scrollTop）—")
    desk = ctx.new_page()
    desk.set_viewport_size({"width": 1600, "height": 900})
    desk.goto(f"{BASE}/?desktop#room=1001", wait_until="load")
    desk.wait_for_timeout(2200)
    dm = desk.evaluate(METRICS)
    check("桌面版初始也在底部（若内容有溢出）",
          dm["scrollHeight"] <= dm["clientHeight"] or dm["atBottom"], dm)

    print("\n— 控制台错误 —")
    print("  无" if not errors else "\n".join("  " + e for e in errors[:6]))
    browser.close()

print("\n结果：" + ("全部通过" if not fails else f"{len(fails)} 项失败 -> {fails}"))
sys.exit(1 if fails else 0)
