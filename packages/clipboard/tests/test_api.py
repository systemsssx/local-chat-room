"""HTTP 接口：快照 / 发消息 / 历史 / 上传。

用 FastAPI 的 TestClient —— **进程内直接调函数**，不启动服务器、不占端口，
所以这一整个文件跑完也就几十毫秒（端到端那套要起真浏览器，慢两个数量级）。
"""
import unittest

from fastapi.testclient import TestClient

from app import config, create_app, db
from tests._base import TempEnvTestCase

# 最小合法 PNG（1×1 透明）—— 服务端只按扩展名/MIME 判类型，不看像素
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)


class ApiTestCase(TempEnvTestCase):
    def setUp(self):
        super().setUp()
        self.client = TestClient(create_app())

    # ── 小工具 ──────────────────────────────────────────

    def post_message(self, room="1001", text="你好", **over):
        body = {"clientId": "c1", "nickname": "测试者", "transport": "polling",
                "type": "text", "source": "text", "text": text}
        body.update(over)
        return self.client.post(f"/api/messages/{room}", json=body)

    def upload(self, name, data, mime, room="1001"):
        return self.client.post(
            "/api/upload",
            files={"file": (name, data, mime)},
            data={"room": room, "clientId": "c1"},
        )


class TestHealthAndNet(ApiTestCase):
    def test_health_结构完整(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertIn("db", body)
        self.assertIn("messages", body["db"])

    def test_health_计数跟着消息走(self):
        self.post_message(text="一条")
        self.post_message(text="两条")
        self.assertEqual(self.client.get("/api/health").json()["db"]["messages"], 2)

    def test_net_默认对局域网开放(self):
        body = self.client.get("/api/net").json()
        self.assertTrue(body["lanOpen"])                 # 默认 HOST=0.0.0.0
        self.assertEqual(body["localUrl"], "http://localhost:8000")
        self.assertNotIn("127.", body["primaryUrl"] or "")   # 给手机用的地址不能是 loopback


class TestSnapshot(ApiTestCase):
    def test_新房间是空的(self):
        body = self.client.get("/api/snapshot/新房间x").json() if False else \
               self.client.get("/api/snapshot/newroom").json()
        self.assertEqual(body["messages"], [])
        self.assertEqual(body["seq"], 0)
        self.assertEqual(body["room"], "newroom")

    def test_发一条后快照能拿到(self):
        self.post_message(text="第一条")
        body = self.client.get("/api/snapshot/1001?since=0").json()
        self.assertEqual(len(body["messages"]), 1)
        self.assertEqual(body["messages"][0]["text"], "第一条")

    def test_since_只回增量(self):
        for i in range(1, 4):
            self.post_message(text=f"第{i}条")
        body = self.client.get("/api/snapshot/1001?since=2").json()
        self.assertEqual([m["seq"] for m in body["messages"]], [3])

    def test_快照带serverTime和members(self):
        self.post_message()
        body = self.client.get("/api/snapshot/1001?clientId=c1&nickname=我").json()
        self.assertIn("serverTime", body)
        self.assertIsInstance(body["members"], list)

    def test_发消息返回ok和完整message(self):
        r = self.post_message(text="返回体")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["message"]["text"], "返回体")
        self.assertTrue(body["message"]["id"])

    def test_xff_头决定ip(self):
        """便于本机模拟"另一台设备"—— 也顺手验证了服务端信任这个头（局域网内网用）"""
        self.client.post("/api/messages/1001", json={
            "clientId": "c9", "nickname": "别人", "transport": "polling",
            "type": "text", "source": "text", "text": "来自另一台",
        }, headers={"x-forwarded-for": "192.168.1.99"})
        msg = self.client.get("/api/snapshot/1001").json()["messages"][0]
        self.assertEqual(msg["ip"], "192.168.1.99")


class TestRoomValidation(ApiTestCase):
    def test_非法房间号_400(self):
        """能进到处理函数的非法名字 → 400"""
        for bad in ("中文房间", "有 空格", "a" * 33):
            r = self.client.get(f"/api/snapshot/{bad}")
            self.assertEqual(r.status_code, 400, f"{bad!r} 应该被拒")

    def test_带斜杠的房间号会变成404(self):
        """斜杠是路径分隔符，请求到不了处理函数 —— 路由直接 404。

        照样是"拒绝"（房间没被创建），只是状态码不同。这里如实记录，免得以后
        有人看到 404 以为是 bug。
        """
        r = self.client.get("/api/snapshot/带/斜杠")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.client.get("/api/snapshot/带/斜杠").json()["detail"],
                         "Not Found")

    def test_空房间号也会被拒(self):
        r = self.client.get("/api/snapshot/")
        self.assertIn(r.status_code, (400, 404))

    def test_被拒的房间号不会留下痕迹(self):
        self.client.get("/api/snapshot/中文房间")
        self.assertIsNone(db.get_room("中文房间"))

    def test_合法房间号都能用(self):
        for good in ("1001", "room_a", "A-b-9", "x"):
            self.assertEqual(self.client.get(f"/api/snapshot/{good}").status_code, 200, good)

    def test_发消息到非法房间也是400(self):
        r = self.post_message(room="非法 房间")
        self.assertEqual(r.status_code, 400)


class TestLimits(ApiTestCase):
    def test_超过64KB返回413(self):
        r = self.post_message(text="x" * (config.MAX_TEXT_BYTES + 1))
        self.assertEqual(r.status_code, 413)
        self.assertIn("64KB", r.json()["detail"])

    def test_错误提示是干净的一句话(self):
        """前端直接把这句显示给用户，不该带 JSON 壳"""
        r = self.post_message(text="x" * (config.MAX_TEXT_BYTES + 1))
        self.assertNotIn("{", r.json()["detail"])

    def test_未知类型返回413(self):
        r = self.post_message(type="乱写的")
        self.assertEqual(r.status_code, 413)

    def test_刚好64KB能过(self):
        self.assertEqual(self.post_message(text="x" * config.MAX_TEXT_BYTES).status_code, 200)


class TestHistory(ApiTestCase):
    def test_历史倒序(self):
        for i in range(1, 6):
            self.post_message(text=f"第{i}条")
        items = self.client.get("/api/history/1001?limit=3").json()["items"]
        self.assertEqual([i["seq"] for i in items], [5, 4, 3])

    def test_历史带label(self):
        self.post_message(text="有正文的")
        item = self.client.get("/api/history/1001").json()["items"][0]
        self.assertEqual(item["label"], "有正文的")
        self.assertEqual(item["type"], "text")

    def test_图片消息的label用文件名(self):
        up = self.upload("界面截图.png", PNG, "image/png").json()["file"]
        self.post_message(type="image", text="", file=up)
        item = self.client.get("/api/history/1001").json()["items"][0]
        self.assertEqual(item["label"], "界面截图.png")

    def test_limit被夹在合理范围(self):
        self.post_message()
        self.assertEqual(self.client.get("/api/history/1001?limit=999").status_code, 200)

    def test_非法房间400(self):
        self.assertEqual(self.client.get("/api/history/非法 房间").status_code, 400)


class TestUpload(ApiTestCase):
    def test_上传图片(self):
        r = self.upload("shot.png", PNG, "image/png")
        self.assertEqual(r.status_code, 200)
        f = r.json()["file"]
        self.assertEqual(f["kind"], "image")
        self.assertEqual(f["size"], len(PNG))
        self.assertTrue(f["url"].startswith("/uploads/"))

    def test_中文文件名不乱码(self):
        """⭐ 踩过的坑：Node 版 busboy 会把 UTF-8 文件名按 latin1 解成 çé¢æªå¾.png"""
        f = self.upload("界面截图.png", PNG, "image/png").json()["file"]
        self.assertEqual(f["name"], "界面截图.png")
        self.assertNotIn("ç", f["name"])

    def test_上传的字节真的落盘了(self):
        f = self.upload("shot.png", PNG, "image/png").json()["file"]
        stored = config.UPLOAD_DIR / f["url"].split("/")[-1]
        self.assertTrue(stored.exists())
        self.assertEqual(stored.read_bytes(), PNG)

    def test_能通过URL取回(self):
        f = self.upload("shot.png", PNG, "image/png").json()["file"]
        r = self.client.get(f["url"])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, PNG)

    def test_纯文本给预览和行数(self):
        data = "第一行\n第二行\n第三行\n".encode("utf-8")
        f = self.upload("notes.txt", data, "text/plain").json()["file"]
        self.assertEqual(f["kind"], "file")
        self.assertEqual(f["encoding"], "UTF-8")
        self.assertEqual(f["lines"], 3)
        self.assertIn("第一行", f["preview"])

    def test_GBK文本能正确识别(self):
        """Windows 记事本存的 .txt 默认是 ANSI/GBK —— 不能解成乱码"""
        data = "会议改到周三 14:00，会议室 B203。\n记得带上流程图。\n".encode("gb18030")
        f = self.upload("会议记录.txt", data, "text/plain").json()["file"]
        self.assertEqual(f["encoding"], "GB18030")
        self.assertIn("会议改到周三", f["preview"])
        self.assertIn("流程图", f["preview"])

    def test_不给mime只看扩展名也能收(self):
        """有些客户端 multipart 部分不带 Content-Type"""
        f = self.upload("code.py", b"print(1)\n", "").json()["file"]
        self.assertEqual(f["kind"], "file")

    def test_不支持的类型被拒(self):
        for name, mime in (("bad.exe", "application/x-msdownload"),
                           ("bad.zip", "application/zip"),
                           ("bad.bin", "application/octet-stream")):
            r = self.upload(name, b"MZ\x90\x00", mime)
            self.assertEqual(r.status_code, 400, name)
            self.assertIn("只接受图片或纯文本文件", r.json()["detail"])

    def test_超过2MB被拒(self):
        r = self.upload("big.png", b"\x00" * (config.MAX_UPLOAD_BYTES + 1024), "image/png")
        self.assertEqual(r.status_code, 413)
        self.assertIn("2MB", r.json()["detail"])

    def test_刚好2MB能过(self):
        r = self.upload("edge.png", b"\x00" * config.MAX_UPLOAD_BYTES, "image/png")
        self.assertEqual(r.status_code, 200)

    def test_上传后能当消息发出去(self):
        f = self.upload("shot.png", PNG, "image/png").json()["file"]
        r = self.post_message(type="image", text="", file=f, source="clipboard")
        self.assertEqual(r.status_code, 200)
        msg = r.json()["message"]
        self.assertEqual(msg["type"], "image")
        self.assertEqual(msg["file"]["name"], "shot.png")
        self.assertEqual(msg["source"], "clipboard")

    def test_上传的文件名里没有路径穿越(self):
        """文件名只取扩展名，不参与落盘路径 —— 恶意 ../ 也不能写到 uploads/ 外面"""
        f = self.upload("../../evil.png", PNG, "image/png").json()["file"]
        stored = config.UPLOAD_DIR / f["url"].split("/")[-1]
        self.assertTrue(stored.exists())
        self.assertEqual(stored.parent, config.UPLOAD_DIR)      # 还在 uploads 里


if __name__ == "__main__":
    unittest.main()
