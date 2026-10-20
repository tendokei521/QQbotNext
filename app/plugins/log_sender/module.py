"""模块声明：日志发送。

聊天里发一条命令就能拿走**本轮次**的运行日志：

- ``/log``  → ``user.log``（作为 ``user.txt`` 发出去）
- ``/logs`` → debug / warn / errors / user 四个文件

命令用的是框架的统一命令系统（`app.plugin.command`）：解析、别名、参数切分、
以及**命中即截断 LLM** 都由框架负责，这里只写业务。取文件、裁剪与发送降级在
`service.py`（文件消息发不出去时，日志正文改走**合并转发**）。

## 为什么要 ``permission = "owner"``

日志里可能有 ws 地址、access_token、群号与聊天片段。这些只该给 Bot 拥有者看，
所以权限定在 owner 档（群里是群主不顶用，看的是拥有者名册）。想让群管理员也能取，
改这一行的 ``permission`` 即可。

**注意 owner 档从哪来**：账号的 ``owner_id``，或 Agent 配置里的 ``agent_owner_list``
名册（跨群生效）。两者都没配时「谁都不是 owner」——本模块会一声不响地不响应
（框架权限过滤静默），而 LLM 会照常出来搭话。排查口径见 ``docs/MODULE_DEV.md §2.5``。
"""

from __future__ import annotations

from app.plugin import BaseModule, command

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
        "fallback_forward_lines": 200,
        "fallback_text_lines": 30,
    }
    config_schema = SCHEMA

    # 两条命令都默认 skip_llm=True（隐藏参数默认值）：回完文件不再让模型搭话。
    @command("log", description="发送本轮次 user 日志（user.txt）")
    async def send_user_log(self, event):
        from .service import send_logs

        await send_logs(self, event, command="log")

    @command("logs", description="发送本轮次四个日志文件（debug/warn/errors/user）")
    async def send_all_logs(self, event):
        from .service import send_logs

        await send_logs(self, event, command="logs")
