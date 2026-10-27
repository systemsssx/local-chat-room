"""把 design/*.html 渲染成 1600x1000 @2x 的 PNG。

用法：python shot.py v1-patch-panel v2-console v3-teletype
需要先起静态服务（根目录 C:\\work），因为字体走同源相对路径加载。
"""
import sys
import pathlib
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8899/collab-clipboard/design"
OUT = pathlib.Path(r"C:\work\collab-clipboard\design\shots")

names = sys.argv[1:]
if not names:
    names = [p.stem for p in sorted(pathlib.Path(r"C:\work\collab-clipboard\design").glob("v*.html"))]

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome")
    ctx = browser.new_context(viewport={"width": 1600, "height": 1000}, device_scale_factor=2)
    for name in names:
        page = ctx.new_page()
        page.goto(f"{BASE}/{name}.html", wait_until="load")
        page.wait_for_timeout(900)          # 等可变字体加载完成
        page.screenshot(path=str(OUT / f"{name}.png"))
        print("shot ->", OUT / f"{name}.png")
        page.close()
    browser.close()
