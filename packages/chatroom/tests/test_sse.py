"""SSE：唯一的实时通道。

两个层面分开测：

  · **访问控制**（HTTP 层）：没登录 / 不是成员 / 房间不存在 —— 这三种在流开始**之前**就返回，
    用 TestClient 直接断言状态码即可；
  · **帧格式与订阅表**（单元层）：`frame()` 拼出来的字符串、`hub` 只推给该收到的人。

为什么不在这里读流：SSE 的响应**永远不会结束**，TestClient 会一直等下去（实测会挂住）。
真正的"连上能收到推送"由 `dev/e2e-check.py` 对着真服务器验证（那边有超时保护）。
"""

import asyncio
import json
import unittest

from app import db
from app.rooms import frame, hub

from ._base import TempEnvTestCase


class TestSseAccess(TempEnvTestCase):
    def test_没登录连不上(self):
        self.assertEqual(self.browser().get("/api/sse").status_code, 401)

    def test_不是成员连不上(self):
        a = self.browser_with_user("阿哲")
        room = self.make_room(a)
        b = self.browser_with_user("老周")
        self.assertEqual(b.get(f"/api/sse?room={room['id']}").status_code, 403)

    def test_房间不存在返回404(self):
        a = self.browser_with_user("阿哲")
        self.assertEqual(a.get("/api/sse?room=9999").status_code, 404)


class TestFrame(unittest.TestCase):
    def test_标准SSE帧格式(self):
        text = frame("messages", {"messages": [{"seq": 1}]})
        self.assertTrue(text.startswith("event: messages\n"))
        self.assertTrue(text.endswith("\n\n"))          # 空行 = 一帧结束
        self.assertEqual(text.count("data: "), 1)
        self.assertIn(json.loads(text.split("data: ", 1)[1].strip()), [{"messages": [{"seq": 1}]}])

    def test_中文不被转义(self):
        """ensure_ascii=False：帧里应该是中文本身，不是 \\uXXXX（省带宽也方便抓包看）"""
        self.assertIn("产品讨论", frame("rooms", {"rooms": [{"name": "产品讨论"}]}))


class TestHubRouting(TempEnvTestCase):
    """订阅表的推送范围 —— 用同步队列直接验，不经过 HTTP（SSE 的流不会结束，读它会挂住）"""

    def test_只推给同房间的订阅者(self):
        mine, other_room, other_user = asyncio.Queue(), asyncio.Queue(), asyncio.Queue()
        hub.subscribe(1, 7, mine)
        hub.subscribe(2, 8, other_room)
        hub.subscribe(1, 9, other_user)

        hub.push_room(1, "messages", {"messages": []})
        self.assertEqual(mine.get_nowait().splitlines()[0], "event: messages")
        self.assertEqual(other_user.get_nowait().splitlines()[0], "event: messages")
        with self.assertRaises(asyncio.QueueEmpty):
            other_room.get_nowait()                    # 别的房间收不到

    def test_用户频道只推给这个人(self):
        mine, other = asyncio.Queue(), asyncio.Queue()
        hub.subscribe(1, 7, mine)
        hub.subscribe(1, 8, other)
        hub.push_user(7, "rooms", {"rooms": []})
        self.assertEqual(mine.get_nowait().splitlines()[0], "event: rooms")
        with self.assertRaises(asyncio.QueueEmpty):
            other.get_nowait()

    def test_在线人数按连接去重(self):
        q1, q2, q3 = asyncio.Queue(), asyncio.Queue(), asyncio.Queue()
        hub.subscribe(1, 7, q1)                        # 同一个人开了两个标签页
        hub.subscribe(1, 7, q2)
        hub.subscribe(1, 8, q3)
        self.assertEqual(hub.online_ids(1), {7, 8})
        self.assertEqual(hub.online_in_room(1), 2)     # 人数是 2，不是连接数 3

        hub.unsubscribe(1, 7, q1)
        self.assertEqual(hub.online_ids(1), {7, 8})    # 还开着一个
        hub.unsubscribe(1, 7, q2)
        self.assertEqual(hub.online_ids(1), {8})       # 都关了
        hub.unsubscribe(1, 8, q3)
        self.assertEqual(hub.online_in_room(1), 0)

    def test_注销不会留下空壳(self):
        q = asyncio.Queue()
        hub.subscribe(1, 7, q)
        hub.unsubscribe(1, 7, q)
        self.assertNotIn(1, hub._room)
        self.assertNotIn(7, hub._user)

    def test_推给成员时不打扰正在看这个房间的人(self):
        """正在看这个房间的人已经从房间频道收到消息了，不用再收一条"未读提醒"。

        （注意：这个推送的收件人是**房间成员**，所以用例里得先有两个真成员。）
        """
        a = self.browser_with_user("阿哲")
        room = self.make_room(a)
        b = self.browser_with_user("老周")
        self.join(b, room["code"])
        user_a = a.get("/api/auth/me").json()["user"]["id"]
        user_b = b.get("/api/auth/me").json()["user"]["id"]

        viewer, away = asyncio.Queue(), asyncio.Queue()
        hub.subscribe(room["id"], user_a, viewer)      # 阿哲正看着这个房间
        hub.subscribe(None, user_b, away)              # 老周在别的页面（只订了用户频道）

        hub.push_members_except(room["id"], "room-updated", {"roomId": room["id"]},
                                skip_viewers_of=room["id"])
        with self.assertRaises(asyncio.QueueEmpty):
            viewer.get_nowait()
        self.assertEqual(away.get_nowait().splitlines()[0], "event: room-updated")

    def test_队列满了不阻塞广播(self):
        """慢客户端只丢它自己的那条，不能拖住别人。"""
        slow = asyncio.Queue(maxsize=1)
        fast = asyncio.Queue()
        hub.subscribe(1, 7, slow)
        hub.subscribe(1, 8, fast)
        hub.push_room(1, "messages", {"messages": [1]})
        hub.push_room(1, "messages", {"messages": [2]})   # slow 满了 → 丢弃
        self.assertEqual(fast.qsize(), 2)
        self.assertEqual(slow.qsize(), 1)


class TestMembersView(TempEnvTestCase):
    def test_没人连SSE时都算离线(self):
        a = self.browser_with_user("阿哲")
        room = self.make_room(a)
        from app.rooms import members_view
        members = members_view(room["id"])
        self.assertEqual(len(members), 1)
        self.assertFalse(members[0]["online"])

    def test_连上SSE就算在线(self):
        a = self.browser_with_user("阿哲")
        room = self.make_room(a)
        from app.rooms import members_view
        queue = asyncio.Queue()
        hub.subscribe(room["id"], a.get("/api/auth/me").json()["user"]["id"], queue)
        try:
            members = members_view(room["id"])
            self.assertTrue(members[0]["online"])
        finally:
            hub._room.clear()
            hub._user.clear()

    def test_房间列表里的人数是成员数不是在线数(self):
        a = self.browser_with_user("阿哲")
        room = self.make_room(a)
        b = self.browser_with_user("老周")
        self.join(b, room["code"])
        item = self.rooms_of(a)[0]
        self.assertEqual(item["members"], 2)
        self.assertEqual(item["online"], 0)            # 没有活跃 SSE 连接
        self.assertEqual(db.member_ids(room["id"]) and len(db.member_ids(room["id"])), 2)


if __name__ == "__main__":
    unittest.main()
