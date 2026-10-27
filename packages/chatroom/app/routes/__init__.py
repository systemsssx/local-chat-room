"""路由包：按职责拆开

    auth_routes.py  —— 注册 / 登录 / 登出 / 我是谁
    room_routes.py  —— 建群 / 凭码入群 / 我的房间列表 / 房间概况 / 退群 / 换码
    api_routes.py   —— 消息：快照 / 发送 / 翻页 / 上传
    sse_routes.py   —— 唯一的实时通道（服务器主动推）
    net_routes.py   —— /api/health、/api/net、连接实况
"""
