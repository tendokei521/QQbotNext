"""模块声明：示例插件（复制本目录改名即可开始写插件）。

main.py 只做三件事：声明身份与配置、接框架的 hook、把具体逻辑交给 src/。
改「接哪些事件」改这里；改「怎么判断/怎么回」去 src/reply.py。
"""

from __future__ import annotations

from app.plugin import BaseModule, module_hook

from .config_schema import SCHEMA
from .src.reply import build_reply, hit_keyword


class Module(BaseModule):
    name = "示例插件"
    sign = "ExamplePlugin"
    description = "示例插件，用来照着写自己的插件"
    permission = "everyone"
    category = "消息"
    subscribe = ("message_group", "message_private")
    default_config = {
        "enable": False,
        "keywords": ["hello"],
    }
    config_schema = SCHEMA

    @module_hook("message_group", order=10)
    @module_hook("message_private", order=10)
    async def handle_message(self, event):
        """命中关键词就回一句；开关与配置都从 self.config 取。"""
        if not self.config.get("enable", False):
            return
        if not hit_keyword(event.message, self.config.get("keywords", [])):
            return
        await self.send_to(event.session_id, build_reply(event))
