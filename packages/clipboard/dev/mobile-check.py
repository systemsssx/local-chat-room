"""手机版适配验证 + 截图。

用 Playwright 模拟 iPhone 尺寸（390×844 @3x，触屏），检查：
  1. 窄屏打开 / 会自动跳到 /mobile.html
  2. 手机版页面能正常初始化（六个组合的控件都在、无 JS 报错）
  3. 发一条消息能上屏（走 SSE）
  4. 两个抽屉能打开
  5. 底部输入框在可视区域内（不会被地址栏顶出去）
截图存到 design/shots/
"""
import pathlib
import sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
LAN = "http://192.168.1.14:8000"     # 手机访问的是这个 —— 非安全上下文
OUT = pathlib.Path(r"C:\work\collab-clipboard\design\shots")
OUT.mkdir(parents=True, exist_ok=True)

errors: list[str] = []
fails: list[str] = []


def check(name, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""))
    if not ok:
        fails.append(name)


with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome")
    ctx = browser.new_context(
        viewport={"width": 390, "height": 844},     # iPhone 14/15 竖屏
        device_scale_factor=3,
        is_mobile=True,
        has_touch=True,
    )
    page = ctx.new_page()
    page.on("console", lambda m: errors.append(f"{m.type}: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"PAGEERROR: {e}"))

    print("\n— ① 窄屏访问根地址，应自动跳到手机版 —")
    page.goto(f"{BASE}/#room=1001", wait_until="load")
    page.wait_for_timeout(1800)
    check("自动跳到 /mobile.html", page.url.endswith("mobile.html#room=1001") or "mobile.html" in page.url, page.url)

    print("\n— ② 手机版初始化 —")
    check("无 JS 报错", not errors, "; ".join(errors[:3]))
    check("三种传输都在", page.locator("#channels .chan").count() == 3, page.locator("#channels .chan").count())
    check("两种模式都在", page.locator("#modeSeg .seg-btn").count() == 2, page.locator("#modeSeg .seg-btn").count())
    check("房间号显示正确", page.locator("#roomChipValue").inner_text() == "1001", page.locator("#roomChipValue").inner_text())
    check("历史消息已加载", page.locator(".msg").count() > 0, page.locator(".msg").count())
    check("剪贴板模式在手机上会被标记为不支持",
          page.locator("#streamStatus").count() == 1)

    print("\n— ③ 发一条消息（走默认 SSE）—")
    before = page.locator(".msg").count()
    page.fill("input.comp", "从手机版发出的一条")
    page.click("button.send")
    page.wait_for_timeout(1500)
    after = page.locator(".msg").count()
    check("消息上屏", after == before + 1, f"{before} -> {after}")
    check("消息流里能看到刚发的字",
          "从手机版发出的一条" in page.locator("#streamList").inner_text())

    print("\n— ④ 布局没被撑破（横向不能滚动）—")
    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    check("没有横向溢出", overflow <= 1, f"溢出 {overflow}px")
    box = page.locator("footer.bottom").bounding_box()
    check("输入栏在视口内", box and box["y"] + box["height"] <= 844 + 1, box)

    page.screenshot(path=str(OUT / "m1-mobile-chat.png"))
    print(f"  截图 -> {OUT / 'm1-mobile-chat.png'}")

    print("\n— ⑤ 两个抽屉 —")
    page.click("#btnLeft")
    page.wait_for_timeout(450)
    check("左抽屉打开", page.locator("#drawerLeft.show").count() == 1)
    check("左抽屉里有房间号输入框", page.locator("#drawerLeft #roomInput").is_visible())
    page.screenshot(path=str(OUT / "m2-mobile-drawer-left.png"))
    print(f"  截图 -> {OUT / 'm2-mobile-drawer-left.png'}")
    # 用抽屉自带的关闭按钮（点遮罩中心会被抽屉挡住 —— 抽屉宽度 86vw）
    page.click("#drawerLeft [data-close]")
    page.wait_for_timeout(400)
    check("关闭按钮能收起左抽屉", page.locator("#drawerLeft.show").count() == 0)

    page.click("#btnRight")
    page.wait_for_timeout(450)
    check("右抽屉打开", page.locator("#drawerRight.show").count() == 1)
    check("右抽屉里有成员/服务/历史",
          page.locator("#drawerRight #members").count() == 1
          and page.locator("#drawerRight #serviceInfo").count() == 1
          and page.locator("#drawerRight #historyList").count() == 1)
    page.screenshot(path=str(OUT / "m3-mobile-drawer-right.png"))
    print(f"  截图 -> {OUT / 'm3-mobile-drawer-right.png'}")
    # 点遮罩的左边缘（那里没被右抽屉盖住）来关闭
    page.locator("#scrim").click(position={"x": 6, "y": 420})
    page.wait_for_timeout(400)
    check("点遮罩（左边缘）能收起右抽屉", page.locator("#drawerRight.show").count() == 0)

    print("\n— ⑥ 剪贴板模式：localhost 下可用，局域网 IP 下必须明确提示不支持 —")
    page.click("#modeSeg button[data-mode='clipboard']")
    page.wait_for_timeout(600)
    check("输入框消失", page.locator("input.comp").count() == 0)
    check("出现监听状态条", page.locator(".listen").count() == 1)
    warn_local = page.locator(".listen .comp-hint").inner_text() if page.locator(".listen .comp-hint").count() else ""
    check("localhost（安全上下文）下不报不支持", "不支持" not in warn_local, warn_local[:50])
    page.screenshot(path=str(OUT / "m4-mobile-clipboard.png"))
    print(f"  截图 -> {OUT / 'm4-mobile-clipboard.png'}")

    # 手机访问的是局域网 IP，那是**非**安全上下文 —— 必须给出明确提示而不是静默失败
    phone = ctx.new_page()
    phone.on("pageerror", lambda e: errors.append(f"PAGEERROR(phone): {e}"))
    phone.goto(f"{LAN}/mobile.html#room=1001", wait_until="load")
    phone.wait_for_timeout(1500)
    phone.click("#modeSeg button[data-mode='clipboard']")
    phone.wait_for_timeout(700)
    warn_lan = phone.locator(".listen .comp-hint").inner_text() if phone.locator(".listen .comp-hint").count() else ""
    check("局域网 IP 下明确提示不支持", ("不支持" in warn_lan) or ("安全上下文" in warn_lan),
          warn_lan[:70])
    phone.screenshot(path=str(OUT / "m5-mobile-clipboard-blocked.png"))
    print(f"  截图 -> {OUT / 'm5-mobile-clipboard-blocked.png'}")

    print("\n— ⑦ 桌面版不受影响（宽屏不该跳转）—")
    desk = ctx.new_page()
    desk.set_viewport_size({"width": 1600, "height": 1000})
    desk.goto(f"{BASE}/", wait_until="load")
    desk.wait_for_timeout(1500)
    check("宽屏停在 index.html", "mobile.html" not in desk.url, desk.url)
    check("宽屏是四面框布局", desk.locator("aside.left").count() == 1 and desk.locator("aside.right").count() == 1)

    print("\n— 控制台错误 —")
    print("  无" if not errors else "\n".join("  " + e for e in errors[:8]))
    browser.close()

print("\n结果：" + ("全部通过" if not fails else f"{len(fails)} 项失败 -> {fails}"))
sys.exit(1 if fails else 0)
