"""数据层：建表 / 读写 / 去重 / 重启后还在不在。

这一层出错最难查 —— 界面上看着"少了一条"或"多了一条"，你根本不知道是内存的问题还是库的问题。
"""
import sqlite3
import unittest

from app import db
from tests._base import TempEnvTestCase


def make_msg(seq, room="1001", text="内容", **over):
    msg = {
        "seq": seq, "id": f"id-{seq}", "room": room, "at": 1700000000000 + seq,
        "type": "text", "text": text, "file": None,
        "clientId": "c1", "nickname": "测试者", "ip": "192.168.1.50",
        "source": "text", "transport": "polling",
    }
    msg.update(over)
    return msg


class TestSchema(TempEnvTestCase):
    def test_建表_两张表都在(self):
        db.open_db()
        rows = db._db().execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {r["name"] for r in rows}
        self.assertIn("rooms", names)        # 房间当前状态表
        self.assertIn("messages", names)     # 历史记录表

    def test_索引也建好了(self):
        db.open_db()
        rows = db._db().execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
        names = {r["name"] for r in rows}
        self.assertIn("idx_messages_room_seq", names)


class TestWriteRead(TempEnvTestCase):
    def setUp(self):
        super().setUp()
        db.open_db()

    def test_写一条能按房间读回来(self):
        db.save_message(make_msg(1, text="你好"))
        got = db.last_messages("1001")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["text"], "你好")
        self.assertEqual(got[0]["room"], "1001")

    def test_字段名是camelCase_和前端契约一致(self):
        db.save_message(make_msg(1))
        got = db.last_messages("1001")[0]
        for key in ("seq", "id", "room", "clientId", "nickname", "ip",
                    "type", "text", "file", "source", "transport", "at"):
            self.assertIn(key, got)

    def test_max_seq_是历史最大值(self):
        for i in (1, 2, 7):
            db.save_message(make_msg(i))
        self.assertEqual(db.max_seq(), 7)

    def test_空库的max_seq是0(self):
        self.assertEqual(db.max_seq(), 0)

    def test_同一个id写两次只留一条(self):
        """主键冲突走 INSERT OR IGNORE —— 幂等，不会因为重发多出一行"""
        db.save_message(make_msg(1, text="第一次"))
        db.save_message(make_msg(1, text="第二次"))
        self.assertEqual(len(db.last_messages("1001")), 1)

    def test_文件字段往返不丢(self):
        f = {"id": "abc", "url": "/uploads/abc.png", "name": "界面截图.png",
             "size": 2928, "mime": "image/png", "kind": "image"}
        db.save_message(make_msg(1, text="", type="image", file=f))
        got = db.last_messages("1001")[0]
        self.assertEqual(got["file"], f)
        self.assertEqual(got["file"]["name"], "界面截图.png")   # 中文不能乱


class TestRoomState(TempEnvTestCase):
    def setUp(self):
        super().setUp()
        db.open_db()

    def test_房间当前状态被最新一条覆盖(self):
        db.save_message(make_msg(1, text="第一条"))
        db.save_message(make_msg(2, text="第二条"))
        room = db.get_room("1001")
        self.assertEqual(room["text"], "第二条")
        self.assertEqual(room["seq"], 2)
        self.assertEqual(room["updated_by"], "测试者")

    def test_图片消息的房间状态记文件名(self):
        """图片消息没有正文，房间当前内容退而记文件名（题目要求①：保存当前内容）"""
        f = {"name": "界面截图.png", "url": "/uploads/x.png", "size": 1, "mime": "image/png", "kind": "image"}
        db.save_message(make_msg(1, text="", type="image", file=f))
        self.assertEqual(db.get_room("1001")["text"], "界面截图.png")

    def test_没写过的房间是None(self):
        self.assertIsNone(db.get_room("不存在的房间"))

    def test_all_rooms_按更新时间倒序(self):
        db.save_message(make_msg(1, room="a", text="a"))
        db.save_message(make_msg(2, room="b", text="b"))
        rooms = [r["room"] for r in db.all_rooms()]
        self.assertEqual(rooms, ["b", "a"])       # b 更新更晚，排前面

    def test_房间之间互不干扰(self):
        db.save_message(make_msg(1, room="a", text="A 的内容"))
        db.save_message(make_msg(2, room="b", text="B 的内容"))
        self.assertEqual(db.get_room("a")["text"], "A 的内容")
        self.assertEqual(db.get_room("b")["text"], "B 的内容")


class TestQueries(TempEnvTestCase):
    def setUp(self):
        super().setUp()
        db.open_db()
        for i in range(1, 26):                    # 25 条
            db.save_message(make_msg(i, text=f"第 {i} 条"))

    def test_last_messages_升序(self):
        got = db.last_messages("1001", limit=5)
        self.assertEqual([m["seq"] for m in got], [21, 22, 23, 24, 25])

    def test_recent_messages_倒序(self):
        got = db.recent_messages("1001", limit=3)
        self.assertEqual([m["seq"] for m in got], [25, 24, 23])

    def test_limit_生效(self):
        self.assertEqual(len(db.recent_messages("1001", limit=7)), 7)
        self.assertEqual(len(db.last_messages("1001", limit=7)), 7)

    def test_查询不会串房间(self):
        db.save_message(make_msg(99, room="别的房间", text="别人的"))
        got = db.last_messages("1001")
        self.assertEqual(len(got), 25)
        self.assertNotIn("别人的", [m["text"] for m in got])


class TestPersistence(TempEnvTestCase):
    def test_重开库数据还在(self):
        """模拟"关掉服务再启动"—— 这是附加任务里最硬的一条"""
        db.open_db()
        db.save_message(make_msg(1, text="重启前写的"))
        db.close_db()

        db.open_db()                               # 同一个路径，重新打开
        got = db.last_messages("1001")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["text"], "重启前写的")

    def test_重开库max_seq不重置(self):
        db.open_db()
        for i in (1, 2, 3):
            db.save_message(make_msg(i))
        db.close_db()

        db.open_db()
        self.assertEqual(db.max_seq(), 3)          # 曾经担心这里会回到 0

    def test_stats_数字对得上(self):
        db.open_db()
        db.save_message(make_msg(1, room="a"))
        db.save_message(make_msg(2, room="b"))
        s = db.stats()
        self.assertEqual(s["messages"], 2)
        self.assertEqual(s["rooms"], 2)

    def test_没打开库时调用会明确报错(self):
        db.close_db()
        with self.assertRaises(RuntimeError):
            db.max_seq()


if __name__ == "__main__":
    unittest.main()
