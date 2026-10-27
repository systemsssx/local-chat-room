"""聊天室 —— 服务入口（Python / FastAPI）

启动：
    python server.py                 默认 0.0.0.0:8000（局域网内可见）
    python server.py --port 9000     换端口
    set HOST=127.0.0.1 && python server.py    只监听本机（局域网连不上，仅调试）

接口文档（FastAPI 自动生成）：http://localhost:8000/docs
"""

import argparse
import sys

# 输出统一成 UTF-8：
#   · 直接跑在终端里时，Windows 控制台走 WriteConsoleW，中文本来就是对的；
#   · 输出被重定向/管道接走时（写日志、被脚本采集），默认会用系统区域编码（中文 Windows 是 cp936），
#     在别的程序看来就是乱码 —— 显式指定 UTF-8 免掉这个坑。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:      # 某些环境下 stdout 不支持 reconfigure，忽略即可
    pass

# 依赖自检放在 import 之前：缺包时给一句人话，而不是一长串 ImportError traceback。
try:
    import uvicorn

    from app import config, db, net
    from app import create_app
except ModuleNotFoundError as err:
    _pkgs = {
        "fastapi": "fastapi",
        "uvicorn": "uvicorn",
        "multipart": "python-multipart",
    }
    _missing = _pkgs.get(err.name, err.name)
    print(f"""
  ✗ 缺少依赖：{_missing}

    第一次运行需要先装依赖（只需一次）：
        python -m pip install -r requirements.txt

    或者单独装这一个：
        python -m pip install {_missing}
""")
    sys.exit(1)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="聊天室服务")
    p.add_argument("--host", default=config.HOST, help="监听地址，默认 0.0.0.0（局域网可见）")
    p.add_argument("--port", type=int, default=config.PORT, help="端口，默认 8000")
    p.add_argument("--open", action="store_true", help="启动后自动打开浏览器（双击启动脚本用）")
    return p.parse_args()


def port_in_use(host: str, port: int) -> bool:
    """先探一下端口有没有被占用，好给出人话提示。

    （不探的话，uvicorn 会抛一长串 traceback，双击启动的用户看不懂。）
    """
    import socket as _socket

    probe = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    try:
        probe.bind(("0.0.0.0" if host in ("0.0.0.0", "::") else host, port))
        return False
    except OSError:
        return True
    finally:
        probe.close()


def banner(host: str, port: int) -> None:
    info = net.access_info(host, port)
    stats = db.stats()
    line = "─" * 62

    rows = ["", "  聊天室 · 服务已启动", line, f"  本机访问    {info['localUrl']}"]

    if not info["lanOpen"]:
        rows.append("  局域网访问  ✗ 未开放")
        rows.append(f"              （当前 HOST={host}，只有本机能连；改成 --host 0.0.0.0 即可对局域网开放）")
    elif not info["addresses"]:
        rows.append("  局域网访问  ✗ 没找到可用的 IPv4 地址（网卡没连上？）")
    else:
        rows.append("  局域网访问  ↓ 手机/平板连同一个 WiFi，浏览器直接输下面的地址")
        for a in info["addresses"]:
            rows.append(f"              {a['url']}")

    rows += [
        line,
        f"  监听        {host}:{port}{'（局域网内可见）' if info['lanOpen'] else '（仅本机）'}",
        f"  实时通道    SSE 一条长连接（浏览器自动带 Cookie 完成鉴权）",
        f"  上传上限    {config.MAX_UPLOAD_BYTES // 1024 // 1024} MB · 图片与纯文本文件（自动识别 UTF-8 / GBK）",
        f"  数据        SQLite · data/app.db（重启不丢：{stats['users']} 个账号 / {stats['rooms']} 个房间 / {stats['messages']} 条消息）",
        f"  接口文档    http://localhost:{port}/docs",
        line,
        "  按 Ctrl+C 停止",
        "",
    ]
    print("\n".join(rows))


def main() -> None:
    args = parse_args()

    if port_in_use(args.host, args.port):
        print(f"""
  ✗ 端口 {args.port} 已被占用
     · 可能已经有一个服务在跑（上一次的窗口没关掉？）
     · 想再开一个  → 换端口：  python server.py --port 8001
     · 想重开这个  → 先关掉占用 {args.port} 的那个窗口
""")
        sys.exit(1)

    app = create_app()
    banner(args.host, args.port)

    if args.open:
        import threading
        import webbrowser
        url = f"http://localhost:{args.port}"
        # 等 1.5 秒再开浏览器，让服务先监听上，免得打开一个打不开的页面
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        access_log=False,      # 自己的日志够用了，关掉逐请求的访问日志保持输出干净
        log_level="warning",
    )


if __name__ == "__main__":
    main()
