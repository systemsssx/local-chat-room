"""房间与消息：seq 递增 / 校验规则 / 复用 / 广播 / 在线成员。

⚠️ 其中 test_冷房间第一条不重复 是**真实 bug 的回归测试**：
   曾经 push() 里先落库再 _ensure()，冷房间第一条会先落库、又被载入内存一次、再 append 一次
   → 内存 3 条、库里 2 条。顺序改成 ensure → save → append 才对。
   这种场景手动很难想到去试（要专门"进一个从没进过的新房间"），正是单元测试的价值。
"""
import asyncio
import unittest

from app import config, db
from app.rooms import Hub, Presence, RoomStore
from tests._base import TempEnvTestCase


class RoomStoreTestCase(TempEnvTestCase):
    def setUp(self):
        super().setUp()
        db.open_db()
        self.store = RoomStore()
        self.store.init()

    def push(self, room, payload, **client):
        info = self.client_info(**client)
        return self.run_async(self.store.push(room, payload, info))

    def text(self, room="1001", body="你好", **over):
        payload = {"type": "text", "text": body, "source": "text"}
        payload.update(over)
        return payload


class TestSeq(RoomStoreTestCase):
    def test_序号从1开始且连续(self):
        seqs = [self.push("1001", self.text(body=f"第{i}条"))["seq"] for i in range(1, 6)]
        self.assertEqual(seqs, [1, 2, 3, 4, 5])

    def test_序号跨房间也是全局唯一(self):
        """seq 是全局单调的，前端才能用它统一去重 —— 不是每个房间各自从 1 开始"""
        a = self.push("roomA", self.text())["seq"]
        b = self.push("roomB", self.text())["seq"]
        self.assertEqual([a, b], [1, 2])

    def test_init_接上历史最大值不重置(self):
        self.push("1001", self.text())
        # 模拟服务重启：新建一个 store，重新 init
        fresh = RoomStore()
        self.assertEqual(fresh.init(), 1)
        msg = self.run_async(fresh.push("1002", self.text(), self.client_info()))
        self.assertEqual(msg["seq"], 2)            # 不是回到 1


class TestColdRoom(RoomStoreTestCase):
    def test_冷房间第一条不重复(self):
        """⭐ 真实 bug 的回归：内存里不能同时出现"库载入的"和"刚 append 的"同一条"""
        self.push("全新房间", self.text(body="第一条"))
        got = self.run_async(self.store.since("全新房间", 0))
        self.assertEqual(len(got), 1)
        self.assertEqual(len(db.last_messages("全新房间")), 1)   # 库里也不能是 2

    def test_冷房间连发三条不重复(self):
        for i in range(1, 4):
            self.push("另一个全新房间", self.text(body=f"第{i}条"))
        got = self.run_async(self.store.since("另一个全新房间", 0))
        self.assertEqual([m["text"] for m in got], ["第1条", "第2条", "第3条"])

    def test_重启后新房间仍不重复(self):
        """库里有历史的情况下，第一次访问这个房间要走"从库载入"这条路"""
        self.push("重启房间", self.text(body="重启前"))
        db.close_db()
        db.open_db()
        fresh = RoomStore()
        fresh.init()
        self.run_async(fresh.push("重启房间", self.text(body="重启后"), self.client_info()))
        got = self.run_async(fresh.since("重启房间", 0))
        self.assertEqual([m["text"] for m in got], ["重启前", "重启后"])


class TestValidation(RoomStoreTestCase):
    """校验规则必须收口在 push()：HTTP 和 WebSocket 两条上行路径都走它"""

    def test_超过64KB要报错(self):
        with self.assertRaises(ValueError) as ctx:
            self.push("1001", self.text(body="x" * (config.MAX_TEXT_BYTES + 1)))
        self.assertIn("64KB", str(ctx.exception))

    def test_刚好64KB可以过(self):
        """边界：正好等于上限应该放行"""
        msg = self.push("1001", self.text(body="x" * config.MAX_TEXT_BYTES))
        self.assertEqual(len(msg["text"]), config.MAX_TEXT_BYTES)

    def test_中文按UTF8字节算长度(self):
        """一个汉字 3 字节 —— 按字符数算的话，60KB 个汉字会被误放行"""
        n = config.MAX_TEXT_BYTES // 3 + 1        # 汉字数刚好超限
        with self.assertRaises(ValueError):
            self.push("1001", self.text(body="汉" * n))

    def test_空文本可以发(self):
        msg = self.push("1001", self.text(body=""))
        self.assertEqual(msg["text"], "")

    def test_未知类型要报错(self):
        with self.assertRaises(ValueError):
            self.push("1001", {"type": "乱写的", "text": "x"})

    def test_图片消息必须带file(self):
        with self.assertRaises(ValueError):
            self.push("1001", {"type": "image", "text": "没有 file 字段"})

    def test_文件消息必须带file(self):
        with self.assertRaises(ValueError):
            self.push("1001", {"type": "file", "text": "没有 file 字段"})

    def test_三种合法类型都能过(self):
        f = {"name": "a.png", "url": "/uploads/a.png", "size": 1, "mime": "image/png", "kind": "image"}
        self.push("1001", {"type": "text", "text": "文字"})
        self.push("1001", {"type": "image", "file": f})
        self.push("1001", {"type": "file", "file": {**f, "kind": "text"}})

    def test_校验失败时序号不能白白涨(self):
        before = self.store.seq
        with self.assertRaises(ValueError):
            self.push("1001", self.text(body="x" * (config.MAX_TEXT_BYTES + 1)))
        self.assertEqual(self.store.seq, before)   # seq 不该被吃掉


class TestSinceAndHistory(RoomStoreTestCase):
    def test_since_只返回更新的(self):
        for i in range(1, 5):
            self.push("1001", self.text(body=f"第{i}条"))
        got = self.run_async(self.store.since("1001", 2))
        self.assertEqual([m["seq"] for m in got], [3, 4])

    def test_since_0_返回全部(self):
        for i in range(1, 4):
            self.push("1001", self.text())
        self.assertEqual(len(self.run_async(self.store.since("1001", 0))), 3)

    def test_since_超过最大值返回空(self):
        self.push("1001", self.text())
        self.assertEqual(self.run_async(self.store.since("1001", 999)), [])

    def test_房间互相隔离(self):
        self.push("roomA", self.text(body="A 的"))
        self.push("roomB", self.text(body="B 的"))
        got = self.run_async(self.store.since("roomA", 0))
        self.assertEqual([m["text"] for m in got], ["A 的"])

    def test_history_走库所以重启后仍在(self):
        for i in range(1, 4):
            self.push("1001", self.text(body=f"第{i}条"))
        fresh = RoomStore()                        # 全新实例，内存是空的
        fresh.init()
        got = self.run_async(fresh.history("1001"))
        self.assertEqual(len(got), 3)
        self.assertEqual(got[0]["text"], "第3条")   # 倒序，最新在前

    def test_消息带上传输与来源标记(self):
        msg = self.push("1001", self.text(), transport="sse")
        self.assertEqual(msg["transport"], "sse")
        self.assertEqual(msg["source"], "text")
        self.assertEqual(msg["ip"], "192.168.1.50")
        self.assertTrue(msg["id"])                 # uuid 不为空


class TestHub(TempEnvTestCase):
    def setUp(self):
        super().setUp()
        self.hub = Hub()

    def test_广播进队列(self):
        q = asyncio.Queue()
        self.hub.add_sse("1001", q)
        self.hub.broadcast_messages("1001", [{"seq": 1, "text": "hi"}])
        self.assertEqual(q.qsize(), 1)
        frame = q.get_nowait()
        self.assertTrue(frame.startswith("event: messages\ndata: "))   # SSE 帧格式
        self.assertIn('"seq": 1', frame)                                # 中文不转义、结构没坏

    def test_ws_队列收到的是json帧(self):
        q = asyncio.Queue()
        self.hub.add_ws("1001", q)
        self.hub.broadcast_messages("1001", [{"seq": 2}])
        import json
        frame = json.loads(q.get_nowait())
        self.assertEqual(frame["type"], "broadcast")

    def test_不同房间不串(self):
        q1, q2 = asyncio.Queue(), asyncio.Queue()
        self.hub.add_sse("1001", q1)
        self.hub.add_sse("1002", q2)
        self.hub.broadcast_messages("1001", [{"seq": 1}])
        self.assertEqual((q1.qsize(), q2.qsize()), (1, 0))

    def test_移除后不再收到(self):
        q = asyncio.Queue()
        self.hub.add_sse("1001", q)
        self.hub.remove_sse("1001", q)
        self.hub.broadcast_messages("1001", [{"seq": 1}])
        self.assertEqual(q.qsize(), 0)
        self.assertEqual(self.hub.sse_count("1001"), 0)

    def test_成员广播两边都收到(self):
        sse, ws = asyncio.Queue(), asyncio.Queue()
        self.hub.add_sse("1001", sse)
        self.hub.add_ws("1001", ws)
        self.hub.broadcast_members("1001", [{"nickname": "张三"}])
        self.assertIn("event: members", sse.get_nowait())
        self.assertIn("张三", ws.get_nowait())      # 中文原样，没有 \\u 转义

    def test_多个订阅者都收到同一份(self):
        qs = [asyncio.Queue() for _ in range(3)]
        for q in qs:
            self.hub.add_ws("1001", q)
        self.hub.broadcast_messages("1001", [{"seq": 9}])
        self.assertTrue(all(q.qsize() == 1 for q in qs))


class TestPresence(TempEnvTestCase):
    def setUp(self):
        super().setUp()
        self.presence = Presence()

    def test_touch后出现在列表(self):
        self.presence.touch("192.168.1.50", "1001", {"clientId": "c1", "nickname": "张三"})
        got = self.presence.list("1001")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["nickname"], "张三")
        self.assertEqual(got[0]["ip"], "192.168.1.50")

    def test_不同房间互相看不到(self):
        self.presence.touch("1.1.1.1", "1001", {"clientId": "c1"})
        self.presence.touch("2.2.2.2", "1002", {"clientId": "c2"})
        self.assertEqual(len(self.presence.list("1001")), 1)
        self.assertEqual(self.presence.list("1001")[0]["clientId"], "c1")

    def test_昵称缺省是匿名(self):
        self.presence.touch("1.1.1.1", "1001", {"clientId": "c1", "nickname": ""})
        self.assertEqual(self.presence.list("1001")[0]["nickname"], "匿名")

    def test_超过TTL就消失(self):
        self.presence.touch("1.1.1.1", "1001", {"clientId": "c1"})
        self.presence._clients["c1"]["at"] -= (config.MEMBER_TTL_SEC + 1) * 1000
        self.assertEqual(self.presence.list("1001"), [])

    def test_长连接还在时即使超时也保留(self):
        """SSE/WS 客户端有长连接，不能因为"最近没说话"就把人踢出在线列表"""
        self.presence.touch("1.1.1.1", "1001", {"clientId": "c1"})
        self.presence.set_connected("c1", True)
        self.presence._clients["c1"]["at"] -= (config.MEMBER_TTL_SEC + 1) * 1000
        got = self.presence.list("1001")
        self.assertEqual(len(got), 1)
        self.assertFalse(got[0]["stale"])          # 有长连接 → 不算"仅凭最近活动推断"

    def test_断开的轮询客户端标stale(self):
        self.presence.touch("1.1.1.1", "1001", {"clientId": "c1"})
        self.assertTrue(self.presence.list("1001")[0]["stale"])

    def test_重复touch不产生重复成员(self):
        for _ in range(5):
            self.presence.touch("1.1.1.1", "1001", {"clientId": "c1"})
        self.assertEqual(len(self.presence.list("1001")), 1)

    def test_改了昵称后跟着变(self):
        self.presence.touch("1.1.1.1", "1001", {"clientId": "c1", "nickname": "旧名"})
        self.presence.touch("1.1.1.1", "1001", {"clientId": "c1", "nickname": "新名"})
        self.assertEqual(self.presence.list("1001")[0]["nickname"], "新名")


if __name__ == "__main__":
    unittest.main()
