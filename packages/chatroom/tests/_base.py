"""测试公共部分。

两条纪律：
  1. **绝不碰 data/app.db**：每个用例把数据目录整个指到一个临时目录，用完删掉；
  2. **绝不启动真实服务器**：HTTP 接口用 FastAPI 的 TestClient（进程内直接调函数），
     所以整套测试跑完不到一秒、不占端口。

    `self.browser()` 每次返回一个**新的"浏览器"**（独立 Cookie 罐）——
     多用户的用例全靠它，比如"A 建群、B 凭码进群"。

跑法（在项目根目录）：
    python -m unittest discover
    python -m unittest discover -v
    python -m unittest tests.test_rooms
"""

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import auth, config, create_app, db
from app.rooms import hub, room_store

PASSWORD = "password123"


class TempEnvTestCase(unittest.TestCase):
    """把 DATA_DIR / DB_FILE / UPLOAD_DIR 全指到临时目录。

    config 是**模块级可变对象**，db.open_db() 是调用时才读 config.DB_FILE，
    所以改 config 就能让整个应用写进临时目录。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

        keys = ("DATA_DIR", "DB_FILE", "UPLOAD_DIR", "PBKDF2_ITERATIONS")
        self._saved = {k: getattr(config, k) for k in keys}

        config.DATA_DIR = self.root / "data"
        config.DB_FILE = config.DATA_DIR / "test.db"
        config.UPLOAD_DIR = self.root / "uploads"
        config.PBKDF2_ITERATIONS = 1000        # 测试里不跑 20 万次，快 200 倍
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

        # 单例重置：数据库连接、消息内存、SSE 订阅表、登录失败计数
        db.close_db()
        room_store._rooms.clear()
        hub._room.clear()
        hub._user.clear()
        auth._fails.clear()

        self.app = create_app()

    def tearDown(self):
        db.close_db()
        room_store._rooms.clear()
        hub._room.clear()
        hub._user.clear()
        auth._fails.clear()
        for key, value in self._saved.items():
            setattr(config, key, value)
        self._tmp.cleanup()

    # ── 小工具 ──────────────────────────────────────────

    def browser(self) -> TestClient:
        """一个新的"浏览器"：独立 Cookie 罐，用来模拟另一个人"""
        return TestClient(self.app)

    def browser_with_user(self, username: str) -> TestClient:
        """一个新浏览器 + 一个注册好的账号 —— 最常用的组合"""
        client = self.browser()
        self.register(client, username)
        return client

    def register(self, client: TestClient, username: str, password: str = PASSWORD) -> dict:
        res = client.post("/api/auth/register", json={"username": username, "password": password})
        self.assertEqual(res.status_code, 201, res.text)
        return res.json()["user"]

    def login(self, client: TestClient, username: str, password: str = PASSWORD) -> dict:
        res = client.post("/api/auth/login", json={"username": username, "password": password})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["user"]

    def make_room(self, client: TestClient, name: str = "产品讨论",
                  visibility: str = "private") -> dict:
        res = client.post("/api/rooms", json={"name": name, "visibility": visibility})
        self.assertEqual(res.status_code, 201, res.text)
        return res.json()["room"]

    def join(self, client: TestClient, code: str) -> dict:
        res = client.post("/api/rooms/join", json={"code": code})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def rooms_of(self, client: TestClient) -> list[dict]:
        res = client.get("/api/rooms")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["rooms"]

    def send(self, client: TestClient, room_id: int, text: str, **extra) -> dict:
        res = client.post(f"/api/messages/{room_id}", json={"type": "text", "text": text, **extra})
        self.assertEqual(res.status_code, 201, res.text)
        return res.json()["message"]

    def snapshot(self, client: TestClient, room_id: int, since: int = 0) -> dict:
        res = client.get(f"/api/snapshot/{room_id}", params={"since": since})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()
