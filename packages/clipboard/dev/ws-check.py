"""WebSocket 容错验证：走不了就**明确拒绝**，绝不能让连接/页面崩掉。

协议层（用 websockets 库直连服务端）：
  ① 正常流程：hello → welcome，send → ack（带回执 id）
  ② 超长文本（70KB > 64KB 上限）→ 收到 error 帧，**连接还活着**
  ③ 非法 JSON / 二进制帧 / 未知类型 → 各回一个 error 帧，连接还活着
  ④ 最后再发一条正常消息 → 仍然成功（证明前面那些没把连接搞死）
  ⑤ 并发两条 → 回执按 id 各归各的（不会串）

对照：同样的超长文本走 HTTP POST → 413（两条上行路径限制一致）

浏览器层：
  ⑥ 切到 WebSocket，发 70KB → 弹出错误提示（而不是未捕获异常 / 卡死），页面还能继续用
"""
import asyncio
import json
import pathlib
import random
import sys

import websockets
from playwright.sync_api import sync_playwright

HTTP = "http://127.0.0.1:8000"
WS = "ws://127.0.0.1:8000"
RUN = random.randint(10000, 99999)
ROOM = f"wscheck{RUN}"
OUT = pathlib.Path(r"C:\work\collab-clipboard\design\shots")
fails: list[str] = []


def check(name, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""))
    if not ok:
        fails.append(name)


async def recv_until(ws, pred, timeout=3.0):
    """读到第一个满足条件的帧（跳过 members/broadcast 这类插进来的）"""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while True:
        left = end - loop.time()
        if left <= 0:
            return None
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=left)
        except (asyncio.TimeoutError, websockets.ConnectionClosed):
            return None
        try:
            msg = json.loads(raw)
        except (TypeError, ValueError):
            msg = {"type": "<binary>"}
        if pred(msg):
            return msg


async def protocol_checks():
    uri = f"{WS}/ws/{ROOM}"
    async with websockets.connect(uri) as ws:
        print("\n— ① 正常流程 —")
        await ws.send(json.dumps({"type": "hello", "id": "h1", "clientId": "wsprobe",
                                  "nickname": "协议测试", "since": 0}))
        welcome = await recv_until(ws, lambda m: m.get("type") == "welcome")
        check("hello → welcome", welcome is not None and welcome.get("id") == "h1",
              welcome and welcome.get("type"))

        await ws.send(json.dumps({"type": "send", "id": "r1",
                                  "payload": {"type": "text", "text": "正常一条", "source": "text"}}))
        ack = await recv_until(ws, lambda m: m.get("type") == "ack" and m.get("id") == "r1")
        check("send → ack 带回执 id", ack is not None and ack.get("seq", 0) > 0,
              ack and f"id={ack.get('id')} seq={ack.get('seq')}")

        print("\n— ② 超长文本：拒绝这一条，但连接必须活着 —")
        await ws.send(json.dumps({"type": "send", "id": "big",
                                  "payload": {"type": "text", "text": "x" * (70 * 1024), "source": "text"}}))
        err = await recv_until(ws, lambda m: m.get("type") == "error" and m.get("id") == "big")
        check("收到 error 帧（明确拒绝）", err is not None, err and err.get("message"))
        check("错误信息说清了原因", err and "64KB" in (err.get("message") or ""),
              err and err.get("message"))

        print("\n— ③ 畸形输入：都只回 error，不拆连接 —")
        await ws.send("{这不是合法 JSON")
        e1 = await recv_until(ws, lambda m: m.get("type") == "error")
        check("非法 JSON → error", e1 is not None, e1 and e1.get("message"))

        await ws.send(b"\x00\x01\x02binary")
        e2 = await recv_until(ws, lambda m: m.get("type") == "error")
        check("二进制帧 → error", e2 is not None, e2 and e2.get("message"))

        await ws.send(json.dumps({"type": "nonsense", "id": "n1"}))
        e3 = await recv_until(ws, lambda m: m.get("type") == "error" and m.get("id") == "n1")
        check("未知类型 → error", e3 is not None, e3 and e3.get("message"))

        print("\n— ④ 折腾完还能正常发（连接没被搞死）—")
        await ws.send(json.dumps({"type": "send", "id": "after",
                                  "payload": {"type": "text", "text": "折腾之后还能发", "source": "text"}}))
        ack2 = await recv_until(ws, lambda m: m.get("type") == "ack" and m.get("id") == "after")
        check("连接仍然可用", ack2 is not None, ack2 and f"seq={ack2.get('seq')}")

        print("\n— ⑤ 并发两条：回执按 id 各归各的 —")
        await ws.send(json.dumps({"type": "send", "id": "c1",
                                  "payload": {"type": "text", "text": "并发一", "source": "text"}}))
        await ws.send(json.dumps({"type": "send", "id": "c2",
                                  "payload": {"type": "text", "text": "并发二", "source": "text"}}))
        a1 = await recv_until(ws, lambda m: m.get("type") == "ack" and m.get("id") == "c1")
        a2 = await recv_until(ws, lambda m: m.get("type") == "ack" and m.get("id") == "c2")
        check("两条各自拿到自己的回执", a1 is not None and a2 is not None,
              f"c1={a1 and a1.get('message', {}).get('text')} c2={a2 and a2.get('message', {}).get('text')}")
        check("两条内容没串", a1 and a2 and a1["message"]["text"] == "并发一" and a2["message"]["text"] == "并发二")

        print("\n— ⑥ 连接级：没有 hello 就 send → 明确拒绝 —")
        async with websockets.connect(f"{WS}/ws/wsprobe{RUN}") as ws2:
            await ws2.send(json.dumps({"type": "send", "id": "x1",
                                       "payload": {"type": "text", "text": "还没加入"}}))
            ex = await recv_until(ws2, lambda m: m.get("type") == "error" and m.get("id") == "x1")
            check("未 hello 就发送 → error", ex is not None, ex and ex.get("message"))


def http_check():
    print("\n— ⑦ 对照：同样的超长文本走 HTTP —")
    import urllib.error
    import urllib.request
    body = json.dumps({"clientId": "httpprobe", "nickname": "HTTP 对照", "transport": "polling",
                       "type": "text", "source": "text", "text": "x" * (70 * 1024)}).encode()
    req = urllib.request.Request(f"{HTTP}/api/messages/{ROOM}", data=body,
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            check("HTTP 也应拒绝", False, f"居然成功了 status={r.status}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        check("HTTP 返回 413", e.code == 413, f"{e.code} {detail[:60]}")

    # 正常大小仍然能过（证明不是把整条路封了）
    body2 = json.dumps({"clientId": "httpprobe", "nickname": "HTTP 对照", "transport": "polling",
                        "type": "text", "source": "text", "text": "正常大小"}).encode()
    req2 = urllib.request.Request(f"{HTTP}/api/messages/{ROOM}", data=body2,
                                  headers={"content-type": "application/json"})
    with urllib.request.urlopen(req2, timeout=5) as r:
        check("正常大小仍然 200", r.status == 200, r.status)


def browser_check():
    print("\n— ⑧ 浏览器：切 WS 发超长内容 → 报错提示，页面不崩 —")
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(f"PAGEERROR: {e}"))
        page.on("console", lambda m: errors.append(f"{m.type}: {m.text}")
                if m.type == "error" and "favicon" not in m.text else None)

        page.goto(f"{HTTP}/?desktop#room={ROOM}", wait_until="load")
        page.wait_for_timeout(2000)
        page.click("#channels .chan:nth-child(3)")          # CH3 = WebSocket
        page.wait_for_timeout(1200)
        check("已切到 WebSocket", "WebSocket" in page.locator("#comboA").inner_text(),
              page.locator("#comboA").inner_text())

        page.fill("input.comp", "x" * (70 * 1024))
        page.click("button.send")
        page.wait_for_timeout(1800)

        toast = page.locator(".toast")
        ttext = toast.inner_text() if toast.count() else ""
        check("弹出了错误提示", toast.count() > 0 and ("失败" in ttext or "超过" in ttext),
              ttext[:80].replace("\n", " "))
        check("提示里说明了原因（64KB）", "64KB" in ttext, ttext[:90].replace("\n", " "))
        check("内容被放回输入框（没白打）", len(page.input_value("input.comp")) > 65000,
              len(page.input_value("input.comp")))

        page.screenshot(path=str(OUT / "w1-ws-rejected.png"))
        print(f"  截图 -> {OUT / 'w1-ws-rejected.png'}")

        # 页面还活着：再发一条正常的
        page.fill("input.comp", "拒绝之后还能正常发")
        page.click("button.send")
        page.wait_for_timeout(1500)
        check("页面仍可用：正常消息发出去了",
              "拒绝之后还能正常发" in page.locator("#streamList").inner_text())
        check("没有未捕获的 JS 异常", not errors, "; ".join(errors[:3]))
        browser.close()


asyncio.run(protocol_checks())
http_check()
browser_check()

print("\n结果：" + ("全部通过" if not fails else f"{len(fails)} 项失败 -> {fails}"))
sys.exit(1 if fails else 0)
