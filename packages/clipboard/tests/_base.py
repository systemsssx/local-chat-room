"""测试公共部分。

两条纪律：
  1. **绝不碰 data/app.db**：每个用例把数据目录整个指到一个临时目录，用完删掉；
  2. **绝不启动真实服务器**：HTTP 接口用 FastAPI 的 TestClient（进程内直接调函数），
     所以整套测试跑完不到一秒、不占端口。

跑法（在项目根目录）：
    python -m unittest discover
    python -m unittest discover -v          # 看每个用例的名字
    python -m unittest tests.test_rooms     # 只跑某一个文件
"""
import asyncio
import tempfile
import unittest
from pathlib import Path

from app import config, db
from app.rooms import presence, room_store


class TempEnvTestCase(unittest.TestCase):
    """把 DATA_DIR / DB_FILE / UPLOAD_DIR 全指到临时目录。

    注意 config 是**模块级可变对象**：db.open_db() 是调用时才读 config.DB_FILE，
    所以在这里改 config 就能让整个应用写进临时目录。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

        # 记下原值，tearDown 时还原（免得影响别的用例）
        self._saved = {k: getattr(config, k) for k in ("DATA_DIR", "DB_FILE", "UPLOAD_DIR")}

        config.DATA_DIR = self.root / "data"
        config.DB_FILE = config.DATA_DIR / "test.db"
        config.UPLOAD_DIR = self.root / "uploads"
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

        # 单例重置：db 的连接、room_store 的内存与序号、presence 的在线名单
        db.close_db()
        room_store._rooms.clear()
        room_store._seq = 0
        presence._clients.clear()

    def tearDown(self):
        db.close_db()
        room_store._rooms.clear()
        room_store._seq = 0
        presence._clients.clear()
        for key, value in self._saved.items():
            setattr(config, key, value)
        self._tmp.cleanup()

    # ── 小工具 ──────────────────────────────────────────

    @staticmethod
    def run_async(coro):
        """跑一个协程（RoomStore 的方法是 async 的）"""
        return asyncio.run(coro)

    @staticmethod
    def client_info(client_id="c1", nickname="测试者", ip="192.168.1.50", transport="polling"):
        return {"clientId": client_id, "nickname": nickname, "ip": ip, "transport": transport}
