"""模块声明：Emoji 回复。

- 监听群消息 Emoji 通知（notice_group_emoji），跟随附加的 Emoji；
- 监听群/私聊消息（message_group / message_private），按关键词给消息添加 Emoji 回应。
"""

from app.modules import BaseModule, module_hook

from .config_schema import SCHEMA


class Module(BaseModule):
    name = "Emoji回复"
    sign = "EmojiReply"
    description = "根据消息关键词发送 Emoji 回应，并跟随群消息附加的 Emoji"
    permission = "everyone"
    category = "消息"
    default_config = {
        "follow_emoji": True,
        "follow_emoji_prob": 0.5,
        "keyword_follow_enable": True,
        "keyword_emoji_list": [],
        "keyword_follow_prob": 0.5,
        "cooldown_seconds": 60,
    }
    config_schema = SCHEMA

    @module_hook("notice_group_emoji", order=10)
    async def handle_emoji_notice(self, event):
        from .service import handle_emoji_notice

        await handle_emoji_notice(self, event)

    @module_hook("message_group", order=10)
    @module_hook("message_private", order=10)
    async def handle_message(self, event):
        from .service import handle_message

        await handle_message(self, event)
