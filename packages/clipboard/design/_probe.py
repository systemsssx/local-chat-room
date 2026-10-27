from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome")
    pg = b.new_page(viewport={"width":800,"height":400})
    pg.set_content("<h1 style='font-family:sans-serif'>ok 中文</h1>")
    print("chrome launched:", b.version)
    print("screenshot test ->", pg.screenshot(path=r"C:\work\collab-clipboard\design\shots\_probe.png") and "written")
    b.close()
