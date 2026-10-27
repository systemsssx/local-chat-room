"""端到端验证：对着**真服务器**跑一遍完整流程（注册 → 建群 → 进群 → 发消息 → SSE 推送 → 隔离）。

为什么不依赖浏览器：
    这台机器上浏览器自动化跑不起来（浏览器多进程 IPC 要命名管道，沙箱里被拒），
    所以这一层用**纯 HTTP** 验证"服务端对不对"，浏览器里的视觉与交互由手动测试清单兜底。
    好处是跑得快（几秒）、只用标准库、任何环境都能跑。

跑法（先起服务）：
    python server.py
    python dev/e2e-check.py                        # 默认 http://127.0.0.1:8000
    python dev/e2e-check.py --base http://192.168.1.14:8000
"""

import argparse
import http.client
import http.cookiejar
import json
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:      # pragma: no cover - 某些环境下 stdout 不支持 reconfigure
    pass

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{detail}]" if detail and not ok else ""))
    return ok


# ══════════════ 最小的 HTTP 客户端（带 Cookie 罐）══════════════

class Client:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )

    def request(self, method: str, path: str, body=None, headers=None, raw=False):
        url = self.base + path
        head = dict(headers or {})
        data = None
        if raw:
            data = body
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
            head["content-type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=head, method=method)
        try:
            with self.opener.open(req, timeout=15) as res:
                payload, status = res.read(), res.status
                ctype = res.headers.get("content-type", "")
        except urllib.error.HTTPError as err:
            payload, status = err.read(), err.code
            ctype = err.headers.get("content-type", "")
        if "json" in ctype and payload:
            try:
                return status, json.loads(payload.decode("utf-8"))
            except json.JSONDecodeError:
                return status, None
        return status, payload

    def cookie_header(self) -> str:
        return "; ".join(f"{c.name}={c.value}" for c in self.jar)

    def register(self, username: str, password: str = "password123"):
        return self.request("POST", "/api/auth/register", {"username": username, "password": password})

    def login(self, username: str, password: str = "password123"):
        return self.request("POST", "/api/auth/login", {"username": username, "password": password})

    def create_room(self, name: str, visibility: str = "private"):
        return self.request("POST", "/api/rooms", {"name": name, "visibility": visibility})

    def join(self, code: str):
        return self.request("POST", "/api/rooms/join", {"code": code})

    def send(self, room_id: int, text: str, **extra):
        return self.request("POST", f"/api/messages/{room_id}", {"type": "text", "text": text, **extra})

    def rooms(self):
        return self.request("GET", "/api/rooms")

    def snapshot(self, room_id: int, since: int = 0):
        return self.request("GET", f"/api/snapshot/{room_id}?since={since}")


# ══════════════ SSE 读取器（后台线程收事件）══════════════

class SseReader(threading.Thread):
    def __init__(self, base: str, cookie: str, room_id: int):
        super().__init__(daemon=True)
        self.host = base.split("//", 1)[1]
        self.cookie = cookie
        self.room_id = room_id
        self.events: list[tuple[str, dict]] = []
        self.status = None
        self.stop_flag = threading.Event()

    def run(self):
        try:
            conn = http.client.HTTPConnection(self.host, timeout=20)
            conn.request("GET", f"/api/sse?room={self.room_id}",
                         headers={"Cookie": self.cookie, "Accept": "text/event-stream"})
            res = conn.getresponse()
            self.status = res.status
            event = None
            while not self.stop_flag.is_set():
                line = res.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace").rstrip("\n")
                if text.startswith("event:"):
                    event = text.split(":", 1)[1].strip()
                elif text.startswith("data:"):
                    try:
                        self.events.append((event or "message",
                                            json.loads(text.split(":", 1)[1].strip())))
                    except json.JSONDecodeError:
                        pass
            conn.close()
        except Exception as err:                     # 连接被主动关掉时属于正常收尾
            if not self.stop_flag.is_set():
                self.status = f"error: {err}"

    def wait_for(self, name: str, timeout: float = 6.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for event, data in list(self.events):
                if event == name:
                    return data
            time.sleep(0.05)
        return None

    def wait_for_message(self, text: str, timeout: float = 6.0):
        """等一条**正文等于 text** 的消息帧。

        连上时服务端会先补发断线期间的消息（一帧里可能好几条），所以不能只看第一帧。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            for event, data in list(self.events):
                if event != "messages":
                    continue
                for msg in data.get("messages", []):
                    if msg.get("text") == text:
                        return msg
            time.sleep(0.05)
        return None

    def stop(self):
        self.stop_flag.set()


# ══════════════ 主流程 ══════════════

def multipart_image(room_id: int, filename: str, content: bytes, mime: str):
    boundary = "----chatroom" + uuid.uuid4().hex
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="roomId"\r\n\r\n',
        f"{room_id}\r\n".encode(),
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode(),
        f"Content-Type: {mime}\r\n\r\n".encode(),
        content,
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    return body, f"multipart/form-data; boundary={boundary}"


def main() -> int:
    parser = argparse.ArgumentParser(description="聊天室端到端验证")
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    base = args.base.rstrip("/")
    stamp = str(int(time.time()))[-6:]
    print(f"\n端到端验证 · {base}\n" + "─" * 58)

    # ① 服务与静态资源
    anon = Client(base)
    try:
        status, health = anon.request("GET", "/api/health")
    except Exception as err:
        print(f"  ✗ 连不上 {base}：{err}\n")
        print("    先起服务：python server.py")
        return 1
    check("服务在跑（/api/health）", status == 200 and health.get("ok") is True, str(status))
    for path, needle in (("/", b"/js/main.js"), ("/mobile.html", b"mobile"),
                         ("/js/main.js", b"import"), ("/css/app.css", b"--acc")):
        status, body = anon.request("GET", path)
        check(f"静态资源 {path}", status == 200 and needle in body, str(status))

    # ② 未登录一律 401
    status, _ = anon.request("GET", "/api/rooms")
    check("未登录访问房间列表 → 401", status == 401, str(status))
    status, _ = anon.request("GET", "/api/sse")
    check("未登录连 SSE → 401", status == 401, str(status))

    # ③ 注册 / 登录 / 我是谁
    a = Client(base)
    status, data = a.register(f"阿哲{stamp}")
    check("注册成功 → 201", status == 201, f"{status} {data}")
    a_name = data["user"]["username"]
    status, me = a.request("GET", "/api/auth/me")
    check("注册后自动登录（/api/auth/me）", status == 200 and me["user"]["username"] == a_name)

    b = Client(base)
    status, _ = b.register(f"老周{stamp}")
    check("第二个账号注册成功", status == 201)

    bad = Client(base)
    status, _ = bad.login(a_name, "wrong-password-here")
    check("密码错 → 401", status == 401, str(status))
    status, _ = bad.login("查无此人", "whatever123")
    check("用户不存在也是 401（提示一致）", status == 401, str(status))

    # ④ 建群
    status, data = a.create_room("产品讨论")
    check("建群 → 201", status == 201, str(status))
    room = data["room"]
    check("房间码 6 位且不含易混字符",
          len(room["code"]) == 6 and not set(room["code"]) & set("01OIL"), room["code"])

    # ⑤ 凭码入群
    status, data = b.join(room["code"])
    check("凭房间码入群", status == 200 and data["joined"] is True, str(status))
    status, data = b.join(room["code"])
    check("重复入群是幂等的", status == 200 and data["joined"] is False)
    status, data = a.rooms()
    check("房主列表里成员数变成 2", data["rooms"][0]["members"] == 2, json.dumps(data)[:120])

    # ⑥ 隔离：第三个人进不来
    c = Client(base)
    c.register(f"路人{stamp}")
    status, _ = c.request("GET", f"/api/snapshot/{room['id']}")
    check("非成员读快照 → 403", status == 403, str(status))
    status, _ = c.send(room["id"], "我不该能发")
    check("非成员发消息 → 403", status == 403, str(status))
    status, _ = c.request("POST", f"/api/rooms/{room['id']}/code")
    check("非成员重置房间码 → 403", status == 403, str(status))

    # ⑥.b 公开房间：列表看得到，但拿不到房间码（否则"凭码入群"就是一句空话）
    status, data = a.create_room("公开的房间", "public")
    public_id = data["room"]["id"]
    status, data = c.request("GET", "/api/rooms/public")
    listed = next((r for r in data.get("rooms", []) if r["id"] == public_id), None)
    check("公开房间出现在公开列表里", listed is not None)
    check("公开列表里不带房间码", listed is not None and "code" not in listed)
    status, _ = c.send(public_id, "没入群就发言")
    check("公开房间没入群同样发不了言", status == 403, str(status))

    # ⑥.c 存活探针不泄露内部信息
    status, data = anon.request("GET", "/api/health")
    check("健康检查只回 ok/uptimeSec", set(data) == {"ok", "uptimeSec"}, str(data))

    # ⑦ 发消息 + 未读
    status, data = a.send(room["id"], "第一条：大家早")
    check("发消息 → 201 且 seq=1", status == 201 and data["message"]["seq"] == 1, str(status))
    status, data = b.rooms()
    check("B 的未读数是 1", data["rooms"][0]["unread"] == 1, json.dumps(data["rooms"][0])[:120])
    status, data = b.snapshot(room["id"])
    check("B 快照能看到这条消息", [m["text"] for m in data["messages"]] == ["第一条：大家早"])
    status, data = b.rooms()
    check("进房间后未读清零", data["rooms"][0]["unread"] == 0)

    # ⑧ SSE 推送
    reader = SseReader(base, b.cookie_header(), room["id"])
    reader.start()
    time.sleep(1.0)
    check("SSE 连接建立（200）", reader.status == 200, str(reader.status))
    hello = reader.wait_for("hello", 4)
    check("SSE 收到 hello", hello is not None and hello.get("roomId") == room["id"])
    check("SSE 顺带推了我的房间列表", reader.wait_for("rooms", 4) is not None)

    a.send(room["id"], "第二条：SSE 应该推过来")
    pushed = reader.wait_for_message("第二条：SSE 应该推过来", 6)
    check("SSE 把新消息推给了 B", pushed is not None,
          json.dumps(reader.events[-2:], ensure_ascii=False)[:200])
    member_event = reader.wait_for("members", 4)
    check("SSE 会推成员列表", member_event is not None and len(member_event["members"]) == 2)

    # ⑨ 差额补齐
    status, data = a.snapshot(room["id"], since=1)
    check("since=1 只拿回第 2 条", [m["seq"] for m in data["messages"]] == [2])

    # ⑩ 上传 + 图片消息
    body, ctype = multipart_image(room["id"], "配色稿.png", b"\x89PNG\r\n\x1a\n" + b"0" * 200, "image/png")
    status, data = b.request("POST", "/api/upload", body, {"content-type": ctype}, raw=True)
    check("上传图片 → 201", status == 201, f"{status} {data}")
    file_info = data["file"]
    status, _ = b.request("POST", f"/api/messages/{room['id']}",
                          {"type": "image", "text": "", "file": file_info})
    check("图片消息发送成功", status == 201, str(status))
    status, data = a.request("GET", f"/api/snapshot/{room['id']}")
    images = [m for m in data["messages"] if m["type"] == "image"]
    check("对方能看到图片消息", len(images) == 1 and images[0]["file"]["url"].startswith("/uploads/"))
    status, _ = a.request("GET", images[0]["file"]["url"])
    check("图片能通过 URL 取到", status == 200, str(status))
    status, _ = c.request("GET", images[0]["file"]["url"])
    check("非成员下载群文件 → 403", status == 403, str(status))
    status, _ = anon.request("GET", images[0]["file"]["url"])
    check("未登录下载群文件 → 401", status == 401, str(status))

    # ⑩.b 危险扩展名与伪造的 file 字段
    body, ctype = multipart_image(room["id"], "evil.html", b"<script>alert(1)</script>", "text/html")
    status, _ = b.request("POST", "/api/upload", body, {"content-type": ctype}, raw=True)
    check("上传 .html → 400（存储型 XSS 挡在入口）", status == 400, str(status))
    status, _ = b.request("POST", f"/api/messages/{room['id']}",
                          {"type": "image", "text": "",
                           "file": {"url": "javascript:alert(document.cookie)", "name": "x.png"}})
    check("伪造 file.url → 400", status == 400, str(status))

    # ⑪ 重置房间码
    status, data = a.request("POST", f"/api/rooms/{room['id']}/code")
    new_code = data["code"]
    check("房主重置房间码", status == 200 and new_code != room["code"])
    status, _ = c.join(room["code"])
    check("旧房间码失效 → 404", status == 404, str(status))
    status, data = c.join(new_code)
    check("新房间码可用", status == 200 and data["joined"] is True)

    # ⑫ 退群 / 列表
    status, _ = c.request("POST", f"/api/rooms/{room['id']}/leave")
    check("退群 → 204", status == 204, str(status))
    status, data = c.rooms()
    check("退群后房间列表里没有它", data["rooms"] == [])
    status, _ = c.request("GET", f"/api/snapshot/{room['id']}")
    check("退群后读房间 → 403", status == 403, str(status))

    # ⑬ 登出
    status, _ = b.request("POST", "/api/auth/logout")
    check("登出 → 204", status == 204, str(status))
    status, _ = b.request("GET", "/api/auth/me")
    check("登出后认不出人 → 401", status == 401, str(status))
    status, _ = b.request("GET", f"/api/snapshot/{room['id']}")
    check("登出后带着旧 Cookie 也读不到房间", status == 401, str(status))

    reader.stop()

    print("─" * 58)
    total = len(PASS) + len(FAIL)
    if FAIL:
        print(f"✗ {len(FAIL)}/{total} 项没通过：")
        for name in FAIL:
            print(f"    · {name}")
        return 1
    print(f"✓ {total} 项全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
