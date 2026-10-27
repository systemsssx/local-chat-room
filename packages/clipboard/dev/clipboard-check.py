"""剪贴板监听模式：三种情况都要给出**对得上**的提示。

  ① 未授权（localhost + 没给权限）→ 提示要指向"允许/聚焦"这类**可恢复**的原因，并给「重试」按钮
                                    （老实现是 readText 一抛错就永久放弃，只丢一句"被拒绝"）
  ② 已授权                      → 复制的内容应该被自动发到房间，带 CLIP 标签
  ③ 非安全上下文（局域网 IP）    → 明确说"非安全上下文，请改用纯文字"，且不给「重试」（重试也没用）
"""
import pathlib
import random
import sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
LAN = "http://192.168.1.14:8000"          # 非安全上下文
# 每次跑用全新房间：否则会读到上一次跑留下的历史（而且还带着上次那个 clientId，
# 会被当成"别人的消息"，把状态条文案搞乱）
RUN = random.randint(10000, 99999)
ROOM_A, ROOM_B, ROOM_C = f"clipA{RUN}", f"clipB{RUN}", f"clipC{RUN}"
OUT = pathlib.Path(r"C:\work\collab-clipboard\design\shots")
fails: list[str] = []


def check(name, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""))
    if not ok:
        fails.append(name)


VIEW = {"width": 1600, "height": 1000}

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome")

    # ── ① 未授权 ──────────────────────────────────────────
    print("\n— ① 未授权（localhost，不给剪贴板权限）—")
    ctx1 = browser.new_context(viewport=VIEW)          # 故意不给 clipboard 权限
    page = ctx1.new_page()
    page.goto(f"{BASE}/?desktop#room={ROOM_A}", wait_until="load")
    page.wait_for_timeout(2000)
    page.click("#modeSeg button[data-mode='clipboard']")
    page.wait_for_timeout(1800)

    check("状态条还在（没崩）", page.locator(".listen").count() == 1)
    warn1 = page.locator(".listen .comp-hint").inner_text()
    check("提示指向可恢复的原因（允许/授权/聚焦）",
          any(k in warn1 for k in ("允许", "授权", "聚焦")), warn1[:90].replace("\n", " "))
    check("没把话说死（不是「当前环境不支持」）", "当前环境不支持" not in warn1)
    retry = page.locator(".listen .ln-toggle").first
    check("给了「重试」按钮", retry.is_visible() and "重试" in retry.inner_text())
    diag = page.locator(".listen .ln-diag").inner_text()
    check("诊断行能看到安全上下文/聚焦/权限", "安全上下文" in diag and "页面聚焦" in diag, diag)
    check("状态文字是「暂时中断」而不是「被拒绝」",
          "被拒绝" not in page.locator(".listen .ln-text").inner_text(),
          page.locator(".listen .ln-text").inner_text())
    page.screenshot(path=str(OUT / "c1-clipboard-denied.png"))
    print(f"  截图 -> {OUT / 'c1-clipboard-denied.png'}")

    # ①b 中途授权：警告必须**自动消失**（而不是留一条僵尸提示）
    print("\n— ①b 中途授权：警告要自动消失，并且立刻能读到 —")
    ctx1.grant_permissions(["clipboard-read", "clipboard-write"])
    page.wait_for_timeout(300)
    page.evaluate("navigator.clipboard.writeText('授权之后应该能读到这段')")
    page.wait_for_timeout(2800)
    warn_after = page.locator(".listen .comp-hint").inner_text().strip()
    check("警告已自动清掉", warn_after == "", warn_after[:70].replace("\n", " "))
    check("状态恢复为监听中", "正在监听" in page.locator(".listen .ln-text").inner_text(),
          page.locator(".listen .ln-text").inner_text())
    check("恢复后内容能发出去", "授权之后应该能读到这段" in page.locator("#streamList").inner_text())
    page.screenshot(path=str(OUT / "c1b-clipboard-recovered.png"))
    print(f"  截图 -> {OUT / 'c1b-clipboard-recovered.png'}")
    ctx1.close()

    # ── ② 已授权 ──────────────────────────────────────────
    print("\n— ② 已授权：复制内容应自动发出去 —")
    ctx2 = browser.new_context(viewport=VIEW, permissions=["clipboard-read", "clipboard-write"])
    page2 = ctx2.new_page()
    page2.goto(f"{BASE}/?desktop#room={ROOM_B}", wait_until="load")
    page2.wait_for_timeout(2000)
    page2.click("#modeSeg button[data-mode='clipboard']")
    page2.wait_for_timeout(1200)
    check("已授权时没有警告", page2.locator(".listen .comp-hint").inner_text().strip() == "",
          page2.locator(".listen .comp-hint").inner_text()[:60])

    page2.evaluate("navigator.clipboard.writeText('这条是从剪贴板自动发出去的')")
    page2.wait_for_timeout(2600)
    stream = page2.locator("#streamList").inner_text()
    check("内容被自动发到房间", "这条是从剪贴板自动发出去的" in stream, stream[:60].replace("\n", " "))
    check("消息带 CLIP 标签", page2.locator(".tp.src").count() >= 1)
    check("状态显示已发送", "已发送" in page2.locator(".listen .ln-last").inner_text(),
          page2.locator(".listen .ln-last").inner_text())
    page2.screenshot(path=str(OUT / "c2-clipboard-works.png"))
    print(f"  截图 -> {OUT / 'c2-clipboard-works.png'}")

    # ── ②b 复制图片：应该自动上传并发出去 ──────────────────
    print("\n— ②b 复制图片：应该自动发出（走 navigator.clipboard.read）—")
    page2.evaluate("""async () => {
      const c = document.createElement('canvas');
      c.width = 80; c.height = 50;
      const g = c.getContext('2d');
      g.fillStyle = '#e8a33d'; g.fillRect(0, 0, 80, 50);
      g.fillStyle = '#151618'; g.font = 'bold 20px sans-serif'; g.fillText('CLIP', 8, 34);
      const blob = await new Promise((r) => c.toBlob(r, 'image/png'));
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
    }""")
    page2.wait_for_timeout(3000)

    imgs = page2.locator(".thumb-img").count()
    check("图片被自动发出并显示为缩略图", imgs >= 1, f"缩略图 {imgs} 张")
    check("状态条显示已发送图片", "已发送图片" in page2.locator(".listen .ln-last").inner_text(),
          page2.locator(".listen .ln-last").inner_text())
    check("图片消息带 CLIP 标签", page2.locator(".tp.src").count() >= 1)
    page2.screenshot(path=str(OUT / "c2b-clipboard-image.png"))
    print(f"  截图 -> {OUT / 'c2b-clipboard-image.png'}")

    # 同一个路径不该重复上传（剪贴板里那张图不变 → 指纹不变 → 跳过）
    before = page2.locator(".msg").count()
    page2.wait_for_timeout(2400)
    check("同一张图不会重复发送", page2.locator(".msg").count() == before,
          f"{before} -> {page2.locator('.msg').count()}")

    # ── ②c Ctrl+V 一个文件：这是唯一能传"文件"的通道 ────────
    print("\n— ②c 在页面上 Ctrl+V 一个文件（粘贴事件是唯一能拿到文件的通道）—")
    before_c = page2.locator(".msg").count()
    page2.evaluate("""() => {
      const dt = new DataTransfer();
      const f = new File(['print("hi from paste")\\n'], 'paste-test.py', { type: 'text/x-python' });
      dt.items.add(f);
      document.dispatchEvent(new ClipboardEvent('paste', {
        clipboardData: dt, bubbles: true, cancelable: true,
      }));
    }""")
    page2.wait_for_timeout(3000)
    after_c = page2.locator(".msg").count()
    check("文件被发出去了", after_c == before_c + 1, f"{before_c} -> {after_c}")
    check("文件卡片显示文件名", "paste-test.py" in page2.locator("#streamList").inner_text())
    check("状态条显示已发送文件", "已发送" in page2.locator(".listen .ln-last").inner_text(),
          page2.locator(".listen .ln-last").inner_text())
    page2.screenshot(path=str(OUT / "c2c-clipboard-paste-file.png"))
    print(f"  截图 -> {OUT / 'c2c-clipboard-paste-file.png'}")

    # ── ②d 粘贴一个不被允许的类型：明确拒绝，不崩 ──────────
    print("\n— ②d 粘贴不支持的类型（.exe）：明确拒绝 —")
    page2.evaluate("""() => {
      const dt = new DataTransfer();
      dt.items.add(new File([new Uint8Array([77, 90, 144, 0])], 'bad.exe',
                            { type: 'application/x-msdownload' }));
      document.dispatchEvent(new ClipboardEvent('paste', {
        clipboardData: dt, bubbles: true, cancelable: true,
      }));
    }""")
    page2.wait_for_timeout(2500)
    warn_paste = page2.locator(".listen .comp-hint").inner_text()
    check("给出了拒绝提示", "只接受图片和纯文本文件" in warn_paste or "失败" in warn_paste,
          warn_paste[:70].replace("\n", " "))
    check("页面没崩（状态条还在）", page2.locator(".listen").count() == 1)

    ctx2.close()

    # ── ③ 非安全上下文 ────────────────────────────────────
    print("\n— ③ 局域网 IP（非安全上下文）：明确不支持 —")
    ctx3 = browser.new_context(viewport=VIEW)
    page3 = ctx3.new_page()
    page3.goto(f"{LAN}/?desktop#room={ROOM_C}", wait_until="load")
    page3.wait_for_timeout(2000)
    page3.click("#modeSeg button[data-mode='clipboard']")
    page3.wait_for_timeout(1500)
    warn3 = page3.locator(".listen .comp-hint").inner_text()
    check("提示「非安全上下文」", "非安全上下文" in warn3, warn3[:70].replace("\n", " "))
    check("建议改用纯文字模式", "纯文字" in warn3)
    check("不显示「重试」（重试也没用）",
          not page3.locator(".listen .ln-toggle").first.is_visible())
    page3.screenshot(path=str(OUT / "c3-clipboard-insecure.png"))
    print(f"  截图 -> {OUT / 'c3-clipboard-insecure.png'}")
    ctx3.close()

    browser.close()

print("\n结果：" + ("全部通过" if not fails else f"{len(fails)} 项失败 -> {fails}"))
sys.exit(1 if fails else 0)
