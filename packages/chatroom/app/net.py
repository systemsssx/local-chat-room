"""局域网地址发现。

服务监听 0.0.0.0 只代表"愿意接受局域网连接"，但**用户得知道该访问哪个 IP**。
这里把可用的 IPv4 找出来，给启动横幅和 /api/net 用。

只用标准库：
  · 先用 UDP socket "连"一个外网地址（不真的发包），让内核挑出默认出口网卡 ——
    这个地址最可能就是手机能连上的那个；
  · 再把主机名解析出来的其它地址一并列出（多网卡时有用）。
"""

import socket


def _primary_ip() -> str | None:
    """内核选出来的默认出口 IP。

    注意：这里没有真的发任何数据包 —— UDP 是无连接的，
    connect() 只是让内核按路由表挑一块网卡并绑定源地址。
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def _is_usable(ip: str) -> bool:
    if ip.startswith("127."):          # loopback，只有本机能用
        return False
    if ip.startswith("169.254."):      # APIPA：没拿到 DHCP 的网卡，永远连不通
        return False
    return True


def lan_addresses() -> list[str]:
    """本机可用于局域网访问的 IPv4 列表（真网卡优先）"""
    found: list[str] = []

    primary = _primary_ip()
    if primary and _is_usable(primary):
        found.append(primary)

    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if _is_usable(ip) and ip not in found:
                found.append(ip)
    except OSError:
        pass

    return found


def access_info(host: str, port: int) -> dict:
    """组装给 /api/net 和启动横幅的地址信息"""
    addresses = lan_addresses()
    return {
        "host": host,
        "port": port,
        "lanOpen": host in ("0.0.0.0", "::"),
        "localUrl": f"http://localhost:{port}",
        "addresses": [{"address": ip, "url": f"http://{ip}:{port}"} for ip in addresses],
        "primaryUrl": f"http://{addresses[0]}:{port}" if addresses else None,
    }
