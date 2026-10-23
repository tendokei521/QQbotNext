"""正向 WebSocket 传输：框架作为客户端连出 OneBot 实现。

## 与 reverse.py 成对

两个方向的传输各自一个文件，形状尽量对称：

- ``forward.py``：这里，``connect_forward`` 打开到 OneBot 实现的连接（正向 WS）；
- ``reverse.py``：框架监听、OneBot 实现连入（反向 WS）。

失败原因要落到用户能看懂的一句话（超时 / 拒绝 / 其它），因为这条日志是「点连接
没反应」时唯一的线索；地址一律经 ``mask_ws_url`` 打码，避免 access_token 进日志。
"""

from __future__ import annotations

import asyncio
from typing import Any

import websockets

from app.core.logger import logger
from app.infrastructure.config.ws_url import mask_ws_url


async def connect_forward(url: str, *, settings: Any, log: Any = None) -> Any | None:
    """连接正向 WS 地址；失败返回 ``None``（原因已写日志）。"""
    log = log or logger
    try:
        return await asyncio.wait_for(
            websockets.connect(
                url,
                ping_interval=getattr(settings, "ws_ping_interval", None),
                ping_timeout=getattr(settings, "ws_ping_timeout", None),
            ),
            timeout=getattr(settings, "ws_connect_timeout", 30),
        )
    except asyncio.TimeoutError:
        log.error(
            f"WebSocket连接超时（{getattr(settings, 'ws_connect_timeout', 30)}秒）: {mask_ws_url(url)}"
        )
    except ConnectionRefusedError:
        log.error(f"WebSocket连接被拒绝（服务未启动或地址错误）: {mask_ws_url(url)}")
    except Exception as e:
        log.error(f"WebSocket连接失败: {mask_ws_url(url)} {e}")
    return None
