"""认证：注册 / 登录 / 登出 / 会话。

这一层最容易出的错，全是"安全上想当然"：
  · 明文存密码、或把哈希吐给前端；
  · "用户不存在"和"密码错"给不同提示（等于告诉别人哪些用户名存在）；
  · 登出只清浏览器 Cookie、不删服务端的会话行（票还能用）；
  · Cookie 少了 HttpOnly / SameSite（被 XSS 偷、被 CSRF 用）。
"""

import unittest

from app import auth, config, db

from ._base import PASSWORD, TempEnvTestCase


class TestRegister(TempEnvTestCase):
    def test_注册成功并自动登录(self):
        c = self.browser()
        user = self.register(c, "阿哲")
        self.assertEqual(user["username"], "阿哲")
        self.assertEqual(c.get("/api/auth/me").status_code, 200)

    def test_注册就发cookie(self):
        c = self.browser()
        res = c.post("/api/auth/register", json={"username": "阿哲", "password": PASSWORD})
        self.assertIn(config.COOKIE_NAME, res.cookies)

    def test_没登录问我是谁返回401(self):
        self.assertEqual(self.browser().get("/api/auth/me").status_code, 401)

    def test_用户名重复返回409(self):
        self.register(self.browser(), "阿哲")
        res = self.browser().post("/api/auth/register",
                                  json={"username": "阿哲", "password": PASSWORD})
        self.assertEqual(res.status_code, 409)

    def test_用户名大小写不同也算重复(self):
        self.register(self.browser(), "ada")
        res = self.browser().post("/api/auth/register",
                                  json={"username": "ADA", "password": PASSWORD})
        self.assertEqual(res.status_code, 409)

    def test_密码太短返回400(self):
        res = self.browser().post("/api/auth/register",
                                  json={"username": "阿哲", "password": "1234567"})
        self.assertEqual(res.status_code, 400)

    def test_用户名不合法返回400(self):
        for bad in ("", "a", "带 空格", "x" * 17, "a@b"):
            res = self.browser().post("/api/auth/register",
                                      json={"username": bad, "password": PASSWORD})
            self.assertEqual(res.status_code, 400, bad)

    def test_中文用户名可以(self):
        self.register(self.browser(), "老周")

    def test_响应里没有密码哈希(self):
        res = self.browser().post("/api/auth/register",
                                  json={"username": "阿哲", "password": PASSWORD})
        self.assertNotIn("pass", res.text.lower().replace("password", ""))

    def test_库里不存明文密码(self):
        self.register(self.browser(), "阿哲")
        row = db.find_user("阿哲")
        self.assertNotIn(PASSWORD, row["pass_hash"])
        self.assertTrue(row["pass_hash"].startswith("pbkdf2_sha256$"))


class TestSessionStorage(TempEnvTestCase):
    def test_库里存的是sid的哈希不是sid本身(self):
        c = self.browser()
        self.register(c, "阿哲")
        sid = c.cookies.get(config.COOKIE_NAME)
        self.assertIsNotNone(sid)
        self.assertIsNone(db.get_session(sid))                  # 明文 sid 查不到
        self.assertIsNotNone(db.get_session(auth.hash_sid(sid)))  # 哈希才查得到

    def test_会话有有效期(self):
        c = self.browser()
        self.register(c, "阿哲")
        sid = c.cookies.get(config.COOKIE_NAME)
        session = db.get_session(auth.hash_sid(sid))
        self.assertGreater(session["expires_at"], db.now_ms())

    def test_过期的会话认不出人(self):
        c = self.browser()
        self.register(c, "阿哲")
        sid = c.cookies.get(config.COOKIE_NAME)
        db._db().execute("UPDATE sessions SET expires_at = 1")   # 手动弄过期
        self.assertIsNone(db.get_session(auth.hash_sid(sid)))
        self.assertEqual(c.get("/api/auth/me").status_code, 401)

    def test_服务端能一次踢掉这个人所有设备(self):
        c1, c2 = self.browser(), self.browser()
        user = self.register(c1, "阿哲")
        self.login(c2, "阿哲")
        self.assertEqual(c2.get("/api/auth/me").status_code, 200)
        db.delete_sessions_of_user(user["id"])
        self.assertEqual(c1.get("/api/auth/me").status_code, 401)
        self.assertEqual(c2.get("/api/auth/me").status_code, 401)


class TestLogin(TempEnvTestCase):
    def test_登录成功(self):
        self.register(self.browser(), "阿哲")
        c = self.browser()
        self.assertEqual(self.login(c, "阿哲")["username"], "阿哲")

    def test_密码错返回401(self):
        self.register(self.browser(), "阿哲")
        res = self.browser().post("/api/auth/login",
                                  json={"username": "阿哲", "password": "wrong-password"})
        self.assertEqual(res.status_code, 401)

    def test_用户不存在和密码错给同一句话(self):
        self.register(self.browser(), "阿哲")
        a = self.browser().post("/api/auth/login",
                                json={"username": "阿哲", "password": "wrong-password"})
        b = self.browser().post("/api/auth/login",
                                json={"username": "查无此人", "password": "wrong-password"})
        self.assertEqual(a.status_code, b.status_code)
        self.assertEqual(a.json()["detail"], b.json()["detail"])

    def test_用户名大小写不敏感(self):
        self.register(self.browser(), "Ada")
        self.assertEqual(self.login(self.browser(), "ada")["username"], "Ada")

    def test_连续失败会被锁(self):
        self.register(self.browser(), "阿哲")
        c = self.browser()
        for _ in range(config.LOGIN_MAX_FAILS):
            self.assertEqual(c.post("/api/auth/login",
                                    json={"username": "阿哲", "password": "bad-pass"}).status_code, 401)
        locked = c.post("/api/auth/login", json={"username": "阿哲", "password": PASSWORD})
        self.assertEqual(locked.status_code, 429)

    def test_登录成功后失败计数清零(self):
        self.register(self.browser(), "阿哲")
        c = self.browser()
        for _ in range(config.LOGIN_MAX_FAILS - 1):
            c.post("/api/auth/login", json={"username": "阿哲", "password": "bad-pass"})
        self.login(c, "阿哲")
        for _ in range(config.LOGIN_MAX_FAILS - 1):
            c.post("/api/auth/login", json={"username": "阿哲", "password": "bad-pass"})
        self.assertEqual(c.post("/api/auth/login",
                                json={"username": "阿哲", "password": PASSWORD}).status_code, 200)


class TestLogout(TempEnvTestCase):
    def test_登出后认不出人(self):
        c = self.browser()
        self.register(c, "阿哲")
        self.assertEqual(c.post("/api/auth/logout").status_code, 204)
        self.assertEqual(c.get("/api/auth/me").status_code, 401)

    def test_登出删掉服务端那一行(self):
        c = self.browser()
        self.register(c, "阿哲")
        sid = c.cookies.get(config.COOKIE_NAME)
        c.post("/api/auth/logout")
        self.assertIsNone(db.get_session(auth.hash_sid(sid)))   # 票作废，不是只删浏览器那头

    def test_登出只影响自己那一台(self):
        c1, c2 = self.browser(), self.browser()
        self.register(c1, "阿哲")
        self.login(c2, "阿哲")
        c1.post("/api/auth/logout")
        self.assertEqual(c1.get("/api/auth/me").status_code, 401)
        self.assertEqual(c2.get("/api/auth/me").status_code, 200)


class TestCookieFlags(TempEnvTestCase):
    def test_cookie是httponly和samesite_lax(self):
        res = self.browser().post("/api/auth/register",
                                  json={"username": "阿哲", "password": PASSWORD})
        raw = res.headers["set-cookie"].lower()
        self.assertIn("httponly", raw)      # JS 读不到 —— XSS 偷不走
        self.assertIn("samesite=lax", raw)  # 别的网站发起的请求不带 —— 挡 CSRF
        self.assertIn("path=/", raw)

    def test_局域网http下不加secure(self):
        """加了 Secure，http 下浏览器根本不会存这张 Cookie —— 演示直接登录不上"""
        res = self.browser().post("/api/auth/register",
                                  json={"username": "阿哲", "password": PASSWORD})
        self.assertNotIn("secure", res.headers["set-cookie"].lower())


class TestPasswordHash(TempEnvTestCase):
    def test_同一个密码两次哈希不一样(self):
        a = auth.hash_password(PASSWORD)
        b = auth.hash_password(PASSWORD)
        self.assertNotEqual(a, b)                    # 每次都有新盐

    def test_校验通过(self):
        stored = auth.hash_password(PASSWORD)
        self.assertTrue(auth.verify_password(PASSWORD, stored))
        self.assertFalse(auth.verify_password("别的密码", stored))

    def test_坏数据不炸(self):
        for bad in ("", "不是哈希", "pbkdf2_sha256$abc$def"):
            self.assertFalse(auth.verify_password(PASSWORD, bad))


if __name__ == "__main__":
    unittest.main()
