"""装配级回归：反向账号在真实容器里能被 `BotService.start()` 拉起来。

单测（test_gateway_reverse_ws.py）直接操控网关，覆盖不到 bootstrap 装配这一段：
`ConfigService.get_bots()` 里的 conn_mode 是否真的传到了网关、auto_connect 的反向
账号开机是否自动监听、shutdown 是否释放端口。这三处任何一个断掉，用户看到的都是
「配置保存成功，但账号永远不亮」。
"""

from __future__ import annotations

import asyncio
import socket

from app.application.bot_service import BotService
from app.infrastructure.config.config_service import ConfigService
from app.infrastructure.onebot.gateway import OneBotGateway


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def test_bootstrap_starts_reverse_listener_from_saved_config(container):
    port = _free_port()
    config_service = container.get(ConfigService)
    await config_service.save_bots([{
        "id": "rev",
        "name": "反向号",
        "ws_url": f"ws://127.0.0.1:{port}/onebot/v11/ws",
        "auto_connect": True,
        "conn_mode": "reverse",
    }])

    service = container.get(BotService)
    gateway = container.get(OneBotGateway)
    await service.start()
    try:
        info = gateway.get_bots_info()[0]
        assert info["conn_mode"] == "reverse", "配置里的连接方向必须传进网关"
        assert info["listening"] is True, "auto_connect 的反向账号开机就要开始监听"
        assert info["status"] == "connecting"
        # 端口真的在听：能被 TCP 连上（随即断开，不跑 OneBot 握手）
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            pass
        # 让服务端把这条半开连接处理完再收尾，否则事件循环关闭后残留 transport 会告警
        await asyncio.sleep(0.05)
    finally:
        await service.shutdown()
    assert gateway.get_bots_info()[0]["listening"] is False, "shutdown 必须释放监听端口"
