"""模块声明：标准演示模块。"""

from app.modules import BaseModule, module_hook
from .config_schema import SCHEMA


class Module(BaseModule):
    name = "示例模块"
    sign = "Example"
    description = "这是一个标准演示模块"
    permission = "everyone"
    category = "消息"
    default_config = {
        "api_key": "",
        "max_retry": 3,
        "timeout": 30.0,
        "enable_feature": False,
        "response_mode": "auto",
        "trigger_keywords": ["hello", "hi"],
        "custom_prompt": "你好，我是 QQBot 助手。",
        "webhook_url": "",
    }
    config_schema = SCHEMA

    @module_hook("message_group", order=10)
    @module_hook("message_private", order=10)
    async def handle(self, event):
        from .service import handle

        await handle(self, event)
