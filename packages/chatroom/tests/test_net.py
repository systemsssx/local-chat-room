"""局域网地址发现（纯函数，最好测的一层）。

这里最容易出的错是"把连不通的地址报给用户"：
  · 127.x（loopback）—— 只有本机能用，手机连它等于连自己
  · 169.254.x（APIPA）—— 网卡没拿到 DHCP，永远连不通
界面和启动横幅都直接显示这些地址，报错了用户就会拿着错地址去试。
"""
import unittest

from app import net


class TestIsUsable(unittest.TestCase):
    def test_loopback被过滤(self):
        for ip in ("127.0.0.1", "127.1.2.3"):
            self.assertFalse(net._is_usable(ip), ip)

    def test_APIPA被过滤(self):
        """169.254.x.x：DHCP 失败时系统自己编的地址，跨设备永远连不通"""
        for ip in ("169.254.1.1", "169.254.200.77"):
            self.assertFalse(net._is_usable(ip), ip)

    def test_正常内网地址可用(self):
        for ip in ("192.168.1.14", "10.0.0.5", "172.16.3.9"):
            self.assertTrue(net._is_usable(ip), ip)

    def test_公网地址也算可用(self):
        self.assertTrue(net._is_usable("8.8.8.8"))


class TestLanAddresses(unittest.TestCase):
    def test_返回的地址里没有loopback和APIPA(self):
        for ip in net.lan_addresses():
            self.assertFalse(ip.startswith("127."), ip)
            self.assertFalse(ip.startswith("169.254."), ip)

    def test_不重复(self):
        addrs = net.lan_addresses()
        self.assertEqual(len(addrs), len(set(addrs)))


class TestAccessInfo(unittest.TestCase):
    def test_监听0000时算对局域网开放(self):
        info = net.access_info("0.0.0.0", 8000)
        self.assertTrue(info["lanOpen"])
        self.assertEqual(info["localUrl"], "http://localhost:8000")

    def test_监听127时不算开放(self):
        """--host 127.0.0.1 只监听本机，局域网内连不上，界面必须说清楚"""
        info = net.access_info("127.0.0.1", 8000)
        self.assertFalse(info["lanOpen"])

    def test_端口写进所有url(self):
        info = net.access_info("0.0.0.0", 9001)
        self.assertIn(":9001", info["localUrl"])
        for a in info["addresses"]:
            self.assertIn(":9001", a["url"])

    def test_primaryUrl取第一个可用地址(self):
        info = net.access_info("0.0.0.0", 8000)
        if info["addresses"]:
            self.assertEqual(info["primaryUrl"], info["addresses"][0]["url"])
        else:
            self.assertIsNone(info["primaryUrl"])

    def test_每个地址都带http前缀(self):
        for a in net.access_info("0.0.0.0", 8000)["addresses"]:
            self.assertTrue(a["url"].startswith("http://"), a["url"])
            self.assertIn(a["address"], a["url"])


if __name__ == "__main__":
    unittest.main()
