"""模块声明：日志发送。

聊天里发一条命令就能拿走**本轮次**的运行日志：

- ``/log``  → ``user.log``（作为 ``user.txt`` 发出去）
- ``/logs`` → debug / warn / errors / user 四个文件

「本轮次」= ``logs/`` 根目录下正在写的那四个文件（6 小时轮转，旧段进
``logs/YYYY-MM-DD-HH/``）。取文件的细节、裁剪与发送降级全在 `service.py`，
本文件只负责声明与接线。

## 为什么要 ``permission = "owner"``

日志里可能有 ws 地址、access_token、群号与聊天片段。这些只该给 Bot 拥有者看，
所以权限定在 owner 档（群里是群主不顶用，看的是拥有者名册）。想让群管理员也能取，
改这一行的 ``permission`` 即可。
"""

from __future__ import annotations

from app.plugin import BaseModule, module_hook

from .config_schema import SCHEMA


class Module(BaseModule):
    name = "日志发送"
    sign = "LogSender"
    description = "聊天里用 /log 取本轮次 user 日志、/logs 取四个日志文件"
    permission = "owner"
    category = "消息"
    order = 70
    default_config = {
        "enable": True,
        "tail_lines": 2000,
        "max_file_mb": 8,
        "fallback_text_lines": 30,
    }
    config_schema = SCHEMA

    @module_hook("message_group", order=5)
    @module_hook("message_private", order=5)
    async def handle_command(self, event):
        from .service import handle

        await handle(self, event)
