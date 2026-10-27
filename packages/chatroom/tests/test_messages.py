"""消息：发送 / 快照 / 翻页 / 上传。

关键的一条：**发送者身份只能来自会话**。
请求体里就算写了 userId / username，也必须被忽略 —— 否则改一行 JS 就能冒充别人。
"""

import unittest

from app import config

from ._base import TempEnvTestCase


class TestSend(TempEnvTestCase):
    def setUp(self):
        super().setUp()
        self.a = self.browser_with_user("阿哲")
        self.room = self.make_room(self.a)

    def test_发一条文字消息(self):
        msg = self.send(self.a, self.room["id"], "大家好")
        self.assertEqual(msg["text"], "大家好")
        self.assertEqual(msg["seq"], 1)
        self.assertEqual(msg["username"], "阿哲")
        self.assertEqual(msg["roomId"], self.room["id"])

    def test_发送者身份来自会话不能伪造(self):
        msg = self.send(self.a, self.room["id"], "我是别人",
                        userId=999, username="冒充的")
        self.assertEqual(msg["username"], "阿哲")
        self.assertNotEqual(msg["userId"], 999)

    def test_seq在房间内递增(self):
        seqs = [self.send(self.a, self.room["id"], f"第{i}条")["seq"] for i in range(3)]
        self.assertEqual(seqs, [1, 2, 3])

    def test_每个房间的seq各算各的(self):
        other = self.make_room(self.a, name="摸鱼区")
        self.send(self.a, self.room["id"], "a")
        first_in_other = self.send(self.a, other["id"], "b")
        self.assertEqual(first_in_other["seq"], 1)

    def test_空消息被拒(self):
        res = self.a.post(f"/api/messages/{self.room['id']}", json={"type": "text", "text": "   "})
        self.assertEqual(res.status_code, 400)

    def test_超长消息被拒(self):
        big = "字" * (config.MAX_TEXT_BYTES // 3 + 100)      # 每字 3 字节，超过 64KB
        res = self.a.post(f"/api/messages/{self.room['id']}", json={"type": "text", "text": big})
        self.assertEqual(res.status_code, 400)

    def test_图片消息必须带file(self):
        res = self.a.post(f"/api/messages/{self.room['id']}", json={"type": "image"})
        self.assertEqual(res.status_code, 400)

    def test_不认识的消息类型被拒(self):
        res = self.a.post(f"/api/messages/{self.room['id']}", json={"type": "video", "text": "x"})
        self.assertEqual(res.status_code, 400)

    def test_没登录不能发(self):
        res = self.browser().post(f"/api/messages/{self.room['id']}",
                                  json={"type": "text", "text": "嗨"})
        self.assertEqual(res.status_code, 401)


class TestSnapshot(TempEnvTestCase):
    def setUp(self):
        super().setUp()
        self.a = self.browser_with_user("阿哲")
        self.room = self.make_room(self.a)
        for i in range(5):
            self.send(self.a, self.room["id"], f"第{i}条")

    def test_快照包含房间消息和成员(self):
        data = self.snapshot(self.a, self.room["id"])
        self.assertEqual(data["room"]["id"], self.room["id"])
        self.assertEqual(len(data["messages"]), 5)
        self.assertEqual(data["latestSeq"], 5)
        self.assertEqual(len(data["members"]), 1)

    def test_since只拿差额(self):
        data = self.snapshot(self.a, self.room["id"], since=3)
        self.assertEqual([m["seq"] for m in data["messages"]], [4, 5])

    def test_since是最大序号时返回空(self):
        data = self.snapshot(self.a, self.room["id"], since=5)
        self.assertEqual(data["messages"], [])

    def test_消息按时间顺序(self):
        texts = [m["text"] for m in self.snapshot(self.a, self.room["id"])["messages"]]
        self.assertEqual(texts, [f"第{i}条" for i in range(5)])


class TestHistory(TempEnvTestCase):
    def setUp(self):
        super().setUp()
        self.a = self.browser_with_user("阿哲")
        self.room = self.make_room(self.a)
        for i in range(25):
            self.send(self.a, self.room["id"], f"第{i}条")

    def test_默认拿最近一页(self):
        data = self.a.get(f"/api/history/{self.room['id']}").json()
        self.assertEqual(len(data["messages"]), min(25, config.PAGE_LIMIT))
        self.assertEqual(data["messages"][-1]["text"], "第24条")

    def test_向上翻页(self):
        first = self.a.get(f"/api/history/{self.room['id']}", params={"limit": 10}).json()
        oldest = first["messages"][0]["seq"]
        self.assertTrue(first["hasMore"])
        second = self.a.get(f"/api/history/{self.room['id']}",
                            params={"before": oldest, "limit": 10}).json()
        self.assertEqual(second["messages"][-1]["seq"], oldest - 1)
        self.assertTrue(all(m["seq"] < oldest for m in second["messages"]))

    def test_翻到头了hasMore是false(self):
        data = self.a.get(f"/api/history/{self.room['id']}",
                          params={"before": 1, "limit": 10}).json()
        self.assertEqual(data["messages"], [])
        self.assertFalse(data["hasMore"])

    def test_limit有上限(self):
        data = self.a.get(f"/api/history/{self.room['id']}", params={"limit": 5000}).json()
        self.assertLessEqual(len(data["messages"]), 100)

    def test_非成员翻不了(self):
        b = self.browser_with_user("老周")
        self.assertEqual(b.get(f"/api/history/{self.room['id']}").status_code, 403)


class TestUpload(TempEnvTestCase):
    def setUp(self):
        super().setUp()
        self.a = self.browser_with_user("阿哲")
        self.room = self.make_room(self.a)

    def _upload(self, name: str, data: bytes, mime: str):
        return self.a.post("/api/upload", data={"roomId": str(self.room["id"])},
                           files={"file": (name, data, mime)})

    def test_上传图片(self):
        res = self._upload("配色稿.png", b"\x89PNG\r\n\x1a\n" + b"0" * 100, "image/png")
        self.assertEqual(res.status_code, 201)
        info = res.json()["file"]
        self.assertEqual(info["kind"], "image")
        self.assertTrue(info["url"].startswith("/uploads/"))

    def test_上传文本文件带预览(self):
        res = self._upload("index.js", "const a = 1;\nconsole.log(a);\n".encode(), "text/javascript")
        info = res.json()["file"]
        self.assertEqual(info["kind"], "file")
        self.assertEqual(info["encoding"], "UTF-8")
        self.assertIn("const a = 1;", info["preview"])
        self.assertEqual(info["lines"], 2)

    def test_GBK文本也能解(self):
        res = self._upload("说明.txt", "中文内容".encode("gb18030"), "text/plain")
        info = res.json()["file"]
        self.assertEqual(info["encoding"], "GB18030")
        self.assertIn("中文内容", info["preview"])

    def test_拒绝exe(self):
        res = self._upload("bad.exe", b"MZ\x90\x00", "application/octet-stream")
        self.assertEqual(res.status_code, 400)

    def test_超过2MB被拒(self):
        res = self._upload("big.txt", b"a" * (config.MAX_UPLOAD_BYTES + 1024), "text/plain")
        self.assertEqual(res.status_code, 413)

    def test_上传的文件真的落盘了(self):
        self._upload("a.txt", b"hello", "text/plain")
        files = list(config.UPLOAD_DIR.glob("*.txt"))
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].read_bytes(), b"hello")

    def test_html文件不允许上传(self):
        """上传 .html 到同源目录 = 存储型 XSS（别人打开链接就在我们的源里跑脚本）"""
        res = self._upload("evil.html", b"<script>alert(1)</script>", "text/html")
        self.assertEqual(res.status_code, 400)
        res = self._upload("evil.svg", b"<svg onload=alert(1)>", "image/svg+xml")
        self.assertEqual(res.status_code, 400)


class TestDownload(TempEnvTestCase):
    """下载上传的文件：先验登录，再验成员身份 —— 不然 uploads/ 就是公开图床"""

    def setUp(self):
        super().setUp()
        self.a = self.browser_with_user("阿哲")
        self.room = self.make_room(self.a)
        res = self.a.post("/api/upload", data={"roomId": str(self.room["id"])},
                          files={"file": ("说明.txt", "你好".encode(), "text/plain")})
        self.info = res.json()["file"]

    def test_成员能下载(self):
        res = self.a.get(self.info["url"])
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content, "你好".encode())

    def test_没登录下载_401(self):
        self.assertEqual(self.browser().get(self.info["url"]).status_code, 401)

    def test_登录了但不是成员下载_403(self):
        b = self.browser_with_user("老周")
        self.assertEqual(b.get(self.info["url"]).status_code, 403)

    def test_进群之后就能下载(self):
        b = self.browser_with_user("老周")
        self.join(b, self.room["code"])
        self.assertEqual(b.get(self.info["url"]).status_code, 200)

    def test_文本文件按附件下发并带nosniff(self):
        res = self.a.get(self.info["url"])
        self.assertIn("attachment", res.headers.get("content-disposition", "").lower())
        self.assertEqual(res.headers.get("x-content-type-options"), "nosniff")

    def test_不存在的文件_404(self):
        self.assertEqual(self.a.get("/uploads/000000000000.txt").status_code, 404)

    def test_目录穿越拿不到别的东西(self):
        for path in ("/uploads/../app/config.py", "/uploads/..%2Fconfig.py"):
            res = self.a.get(path)
            self.assertIn(res.status_code, (400, 404), path)


class TestFileField(TempEnvTestCase):
    """消息里的 file 字段是客户端拼的 JSON —— 必须白名单重建，不能整段入库"""

    def setUp(self):
        super().setUp()
        self.a = self.browser_with_user("阿哲")
        self.room = self.make_room(self.a)

    def _send_file(self, payload, kind="file"):
        return self.a.post(f"/api/messages/{self.room['id']}",
                           json={"type": kind, "text": "", "file": payload})

    def test_伪造的file_url一律被拒(self):
        for bad in ("javascript:alert(1)", "http://evil.example/x.png",
                    "/uploads/../../etc/passwd", "/uploads/根本没有这个文件.png", ""):
            res = self._send_file({"url": bad, "name": "x.png"}, kind="image")
            self.assertEqual(res.status_code, 400, bad)

    def test_引用别的房间的文件会被拒(self):
        other = self.make_room(self.a, name="摸鱼区")
        info = self.a.post("/api/upload", data={"roomId": str(other["id"])},
                           files={"file": ("x.txt", b"hi", "text/plain")}).json()["file"]
        res = self._send_file(info)
        self.assertEqual(res.status_code, 400)

    def test_多余字段被丢掉_preview被截断(self):
        info = self.a.post("/api/upload", data={"roomId": str(self.room["id"])},
                           files={"file": ("a.txt", b"hi", "text/plain")}).json()["file"]
        res = self._send_file({**info, "evil": "x" * 5000, "preview": "p" * 9999})
        self.assertEqual(res.status_code, 201)
        stored = res.json()["message"]["file"]
        self.assertNotIn("evil", stored)
        self.assertLessEqual(len(stored["preview"]), 2000)
        self.assertTrue(stored["url"].startswith("/uploads/"))

    def test_文件名被截断(self):
        info = self.a.post("/api/upload", data={"roomId": str(self.room["id"])},
                           files={"file": ("a.txt", b"hi", "text/plain")}).json()["file"]
        res = self._send_file({**info, "name": "长" * 500})
        self.assertLessEqual(len(res.json()["message"]["file"]["name"]), 120)


if __name__ == "__main__":
    unittest.main()
