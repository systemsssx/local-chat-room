"""房间：建群 / 凭码入群 / 列表 / 成员 / 退群 / 换码 / 房间之间的隔离。

这一层的核心是**隔离**：一个群的消息，不是成员就一个字都读不到、也发不进去。
每条用例都尽量从"另一个人"的浏览器去试。
"""

import unittest

from app import config, db

from ._base import TempEnvTestCase


class TestCreateRoom(TempEnvTestCase):
    def test_建群返回房间码(self):
        room = self.make_room(self.browser_with_user("阿哲"))
        self.assertEqual(room["name"], "产品讨论")
        self.assertEqual(len(room["code"]), config.ROOM_CODE_LEN)
        self.assertEqual(room["ownerName"], "阿哲")

    def test_建群的人自动成为成员和房主(self):
        c = self.browser_with_user("阿哲")
        room = self.make_room(c)
        self.assertEqual(room["members"], 1)
        detail = c.get(f"/api/rooms/{room['id']}").json()
        self.assertEqual(detail["room"]["ownerId"], detail["members"][0]["userId"])
        self.assertEqual(detail["members"][0]["role"], "owner")

    def test_房间码字符集不含易混字符(self):
        c = self.browser_with_user("阿哲")
        for i in range(12):
            code = self.make_room(c, name=f"房间{i}")["code"]
            self.assertTrue(set(code) <= set(config.ROOM_CODE_ALPHABET), code)
            self.assertFalse(set(code) & set("01OIL"), code)

    def test_房间码互不相同(self):
        c = self.browser_with_user("阿哲")
        codes = {self.make_room(c, name=f"房间{i}")["code"] for i in range(10)}
        self.assertEqual(len(codes), 10)

    def test_房间名不能为空(self):
        c = self.browser_with_user("阿哲")
        self.assertEqual(c.post("/api/rooms", json={"name": "   "}).status_code, 400)

    def test_房间名太长(self):
        c = self.browser_with_user("阿哲")
        res = c.post("/api/rooms", json={"name": "长" * (config.ROOM_NAME_MAX + 1)})
        self.assertEqual(res.status_code, 400)

    def test_没登录不能建房(self):
        self.assertEqual(self.browser().post("/api/rooms", json={"name": "产品讨论"}).status_code, 401)

    def test_新建的房间出现在我的房间列表里(self):
        c = self.browser_with_user("阿哲")
        room = self.make_room(c)
        rooms = self.rooms_of(c)
        self.assertEqual([r["id"] for r in rooms], [room["id"]])
        self.assertEqual(rooms[0]["unread"], 0)


class TestJoin(TempEnvTestCase):
    def test_凭码入群(self):
        a = self.browser_with_user("阿哲")
        room = self.make_room(a)
        b = self.browser_with_user("老周")
        result = self.join(b, room["code"])
        self.assertTrue(result["joined"])
        self.assertEqual(result["room"]["id"], room["id"])

    def test_重复入群是幂等的(self):
        a = self.browser_with_user("阿哲")
        room = self.make_room(a)
        b = self.browser_with_user("老周")
        self.join(b, room["code"])
        again = self.join(b, room["code"])
        self.assertFalse(again["joined"])
        self.assertEqual(len(db.member_ids(room["id"])), 2)      # 不会插出第二行

    def test_房间码大小写不敏感(self):
        a = self.browser_with_user("阿哲")
        room = self.make_room(a)
        b = self.browser_with_user("老周")
        self.join(b, room["code"].lower())

    def test_房间码无效(self):
        b = self.browser_with_user("老周")
        res = b.post("/api/rooms/join", json={"code": "ZZZZZZ"})
        self.assertEqual(res.status_code, 404)

    def test_入群后出现在我的房间列表(self):
        a = self.browser_with_user("阿哲")
        room = self.make_room(a)
        b = self.browser_with_user("老周")
        self.join(b, room["code"])
        rooms = self.rooms_of(b)
        self.assertIn(room["id"], [r["id"] for r in rooms])
        self.assertEqual(rooms[0]["role"], "member")

    def test_入群后房主那边的成员数变了(self):
        a = self.browser_with_user("阿哲")
        room = self.make_room(a)
        b = self.browser_with_user("老周")
        self.join(b, room["code"])
        self.assertEqual(self.rooms_of(a)[0]["members"], 2)

    def test_没登录不能入群(self):
        a = self.browser_with_user("阿哲")
        room = self.make_room(a)
        res = self.browser().post("/api/rooms/join", json={"code": room["code"]})
        self.assertEqual(res.status_code, 401)


class TestIsolation(TempEnvTestCase):
    """隔离：不是成员，什么都拿不到"""

    def setUp(self):
        super().setUp()
        self.a = self.browser_with_user("阿哲")
        self.b = self.browser_with_user("老周")
        self.room = self.make_room(self.a)

    def test_非成员读不了快照(self):
        self.assertEqual(self.b.get(f"/api/snapshot/{self.room['id']}").status_code, 403)

    def test_非成员发不了消息(self):
        res = self.b.post(f"/api/messages/{self.room['id']}", json={"type": "text", "text": "嗨"})
        self.assertEqual(res.status_code, 403)

    def test_非成员看不了房间详情(self):
        self.assertEqual(self.b.get(f"/api/rooms/{self.room['id']}").status_code, 403)

    def test_非成员传不了文件(self):
        res = self.b.post("/api/upload",
                          data={"roomId": str(self.room["id"])},
                          files={"file": ("a.txt", b"hello", "text/plain")})
        self.assertEqual(res.status_code, 403)

    def test_房间不存在返回404(self):
        self.assertEqual(self.a.get("/api/snapshot/99999").status_code, 404)

    def test_两个房间的消息互不可见(self):
        other = self.make_room(self.a, name="摸鱼区")
        self.send(self.a, self.room["id"], "产品讨论里的消息")
        self.send(self.a, other["id"], "摸鱼区里的消息")
        first = self.snapshot(self.a, self.room["id"])["messages"]
        second = self.snapshot(self.a, other["id"])["messages"]
        self.assertEqual([m["text"] for m in first], ["产品讨论里的消息"])
        self.assertEqual([m["text"] for m in second], ["摸鱼区里的消息"])

    def test_入群之后才看得到(self):
        self.send(self.a, self.room["id"], "只给成员看")
        self.assertEqual(self.b.get(f"/api/snapshot/{self.room['id']}").status_code, 403)
        self.join(self.b, self.room["code"])
        texts = [m["text"] for m in self.snapshot(self.b, self.room["id"])["messages"]]
        self.assertEqual(texts, ["只给成员看"])


class TestMembers(TempEnvTestCase):
    def test_成员列表和在线标记(self):
        a = self.browser_with_user("阿哲")
        room = self.make_room(a)
        b = self.browser_with_user("老周")
        self.join(b, room["code"])
        members = a.get(f"/api/rooms/{room['id']}").json()["members"]
        self.assertEqual({m["username"] for m in members}, {"阿哲", "老周"})
        self.assertTrue(all(m["online"] is False for m in members))   # 没人开 SSE，都不在线

    def test_退群后列表里没有了(self):
        a = self.browser_with_user("阿哲")
        room = self.make_room(a)
        b = self.browser_with_user("老周")
        self.join(b, room["code"])
        self.assertEqual(b.post(f"/api/rooms/{room['id']}/leave").status_code, 204)
        self.assertEqual(self.rooms_of(b), [])
        self.assertEqual(b.get(f"/api/snapshot/{room['id']}").status_code, 403)

    def test_房主退群会把房主让给下一个人(self):
        a = self.browser_with_user("阿哲")
        room = self.make_room(a)
        b = self.browser_with_user("老周")
        self.join(b, room["code"])
        a.post(f"/api/rooms/{room['id']}/leave")
        detail = b.get(f"/api/rooms/{room['id']}").json()
        self.assertEqual(detail["room"]["ownerName"], "老周")
        self.assertEqual(detail["members"][0]["role"], "owner")

    def test_最后一个人退群房间被删掉(self):
        a = self.browser_with_user("阿哲")
        room = self.make_room(a)
        a.post(f"/api/rooms/{room['id']}/leave")
        self.assertIsNone(db.room_by_id(room["id"]))
        self.assertEqual(self.rooms_of(a), [])


class TestRoomCode(TempEnvTestCase):
    def test_只有房主能重置房间码(self):
        a = self.browser_with_user("阿哲")
        room = self.make_room(a)
        b = self.browser_with_user("老周")
        self.join(b, room["code"])
        self.assertEqual(b.post(f"/api/rooms/{room['id']}/code").status_code, 403)

    def test_重置后旧码失效新码可用(self):
        a = self.browser_with_user("阿哲")
        room = self.make_room(a)
        new_code = a.post(f"/api/rooms/{room['id']}/code").json()["code"]
        self.assertNotEqual(new_code, room["code"])
        c = self.browser_with_user("小满")
        self.assertEqual(c.post("/api/rooms/join", json={"code": room["code"]}).status_code, 404)
        self.assertTrue(self.join(c, new_code)["joined"])

    def test_不是成员也能重置吗_不能(self):
        a = self.browser_with_user("阿哲")
        room = self.make_room(a)
        b = self.browser_with_user("老周")
        self.assertEqual(b.post(f"/api/rooms/{room['id']}/code").status_code, 403)


class TestDissolve(TempEnvTestCase):
    def test_房主解散房间(self):
        a = self.browser_with_user("阿哲")
        room = self.make_room(a)
        b = self.browser_with_user("老周")
        self.join(b, room["code"])
        self.assertEqual(a.delete(f"/api/rooms/{room['id']}").status_code, 204)
        self.assertIsNone(db.room_by_id(room["id"]))
        self.assertEqual(self.rooms_of(b), [])

    def test_非房主不能解散(self):
        a = self.browser_with_user("阿哲")
        room = self.make_room(a)
        b = self.browser_with_user("老周")
        self.join(b, room["code"])
        self.assertEqual(b.delete(f"/api/rooms/{room['id']}").status_code, 403)


class TestUnread(TempEnvTestCase):
    def test_未读数按房间算(self):
        a = self.browser_with_user("阿哲")
        room = self.make_room(a)
        b = self.browser_with_user("老周")
        self.join(b, room["code"])
        self.send(a, room["id"], "第一条")
        self.send(a, room["id"], "第二条")
        self.assertEqual(self.rooms_of(b)[0]["unread"], 2)

    def test_进房间看过之后未读清零(self):
        a = self.browser_with_user("阿哲")
        room = self.make_room(a)
        b = self.browser_with_user("老周")
        self.join(b, room["code"])
        self.send(a, room["id"], "给老周的消息")
        self.snapshot(b, room["id"])
        self.assertEqual(self.rooms_of(b)[0]["unread"], 0)

    def test_自己的消息不算未读(self):
        a = self.browser_with_user("阿哲")
        room = self.make_room(a)
        self.send(a, room["id"], "我自己发的")
        self.assertEqual(self.rooms_of(a)[0]["unread"], 0)


class TestRoomListPreview(TempEnvTestCase):
    def test_列表里有最后一条的摘要(self):
        a = self.browser_with_user("阿哲")
        room = self.make_room(a)
        self.send(a, room["id"], "今天把登录做完了")
        item = self.rooms_of(a)[0]
        self.assertIn("阿哲", item["lastText"])
        self.assertIn("今天把登录做完了", item["lastText"])
        self.assertEqual(item["lastSeq"], 1)

    def test_图片消息的摘要是图片两个字(self):
        a = self.browser_with_user("阿哲")
        room = self.make_room(a)
        up = a.post("/api/upload", data={"roomId": str(room["id"])},
                    files={"file": ("a.png", b"\x89PNG\r\n", "image/png")}).json()["file"]
        self.send(a, room["id"], "", type="image", file=up)
        self.assertIn("[图片]", self.rooms_of(a)[0]["lastText"])


class TestPublicRooms(TempEnvTestCase):
    """公开房间：能被看到，**但不给房间码** —— 否则"凭码入群"就是一句空话"""

    def test_公开房间出现在列表里(self):
        a = self.browser_with_user("阿哲")
        self.make_room(a, name="公开的房间", visibility="public")
        b = self.browser_with_user("老周")
        rooms = b.get("/api/rooms/public").json()["rooms"]
        self.assertEqual([r["name"] for r in rooms], ["公开的房间"])

    def test_列表里不带房间码(self):
        a = self.browser_with_user("阿哲")
        self.make_room(a, name="公开的房间", visibility="public")
        b = self.browser_with_user("老周")
        item = b.get("/api/rooms/public").json()["rooms"][0]
        self.assertNotIn("code", item)                 # ★ 关键：不给码
        self.assertEqual(item["ownerName"], "阿哲")

    def test_私密房间不出现(self):
        a = self.browser_with_user("阿哲")
        self.make_room(a, name="私密的", visibility="private")
        b = self.browser_with_user("老周")
        self.assertEqual(b.get("/api/rooms/public").json()["rooms"], [])

    def test_没登录看不了(self):
        self.assertEqual(self.browser().get("/api/rooms/public").status_code, 401)

    def test_搜索里的通配符不当通配符用(self):
        a = self.browser_with_user("阿哲")
        self.make_room(a, name="100%纯天然", visibility="public")
        self.make_room(a, name="别的房间", visibility="public")
        b = self.browser_with_user("老周")
        # 搜 "%"：只有名字里真的有 % 的那个房间该被搜到（不转义的话会匹配全部）
        names = [r["name"] for r in b.get("/api/rooms/public", params={"q": "%"}).json()["rooms"]]
        self.assertEqual(names, ["100%纯天然"])


class TestHealth(TempEnvTestCase):
    def test_存活探针不泄露内部信息(self):
        res = self.browser().get("/api/health").json()
        self.assertEqual(set(res), {"ok", "uptimeSec"})
        self.assertTrue(res["ok"])


if __name__ == "__main__":
    unittest.main()
