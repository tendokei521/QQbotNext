"""Emoji 回复插件测试。

覆盖：关键词列表解析、消息关键词发送 Emoji、跟随群消息 Emoji、同一消息冷却去重。
"""

from types import SimpleNamespace

from app.infrastructure.cache import Cache
from module.modules.emoji_reply.service import (
    handle_emoji_notice,
    handle_message,
    parse_keyword_emoji_list,
)


class FakeConfig:
    def __init__(self, data: dict) -> None:
        self.data = data

    def get(self, key: str, default=None):
        return self.data.get(key, default)


class FakeBot:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str]] = []

    async def set_msg_emoji_like(self, message_id: int, emoji_id: str) -> dict:
        self.calls.append((message_id, emoji_id))
        return {"status": "ok"}


def _make_module(config: dict) -> SimpleNamespace:
    return SimpleNamespace(
        bot_id=1,
        name="Emoji回复",
        config=FakeConfig(config),
        ctx=SimpleNamespace(services=SimpleNamespace(cache=Cache())),
    )


def test_parse_keyword_emoji_list():
    assert parse_keyword_emoji_list(["哈哈:123", "坏格式", "", "test:456"]) == [
        ("哈哈", "123"),
        ("test", "456"),
    ]
    assert parse_keyword_emoji_list([]) == []


async def test_handle_message_sends_matching_emoji_and_cooldown():
    module = _make_module({
        "keyword_follow_enable": True,
        "keyword_emoji_list": ["哈哈:123", "再见:456"],
        "keyword_follow_prob": 1.0,
        "cooldown_seconds": 60,
    })
    bot = FakeBot()
    event = SimpleNamespace(message_id=100, text="今天哈哈哈", bot=bot)

    await handle_message(module, event)
    assert bot.calls == [(100, "123")]

    # 同一消息 ID 在冷却期内不重复处理
    await handle_message(module, event)
    assert bot.calls == [(100, "123")]


async def test_handle_message_disabled_or_no_keyword():
    module = _make_module({
        "keyword_follow_enable": True,
        "keyword_emoji_list": ["哈哈:123"],
        "keyword_follow_prob": 1.0,
        "cooldown_seconds": 0,
    })
    bot = FakeBot()
    event = SimpleNamespace(message_id=101, text="今天吃什么", bot=bot)

    await handle_message(module, event)
    assert bot.calls == []

    module.config.data["keyword_follow_enable"] = False
    event.text = "哈哈"
    await handle_message(module, event)
    assert bot.calls == []


async def test_handle_emoji_notice_follows_and_dedup():
    module = _make_module({
        "follow_emoji": True,
        "follow_emoji_prob": 1.0,
        "cooldown_seconds": 60,
    })
    bot = FakeBot()
    event = SimpleNamespace(
        message_id=200,
        emoji_is_add=True,
        emoji_likes=[{"emoji_id": "123", "count": 1}],
        bot=bot,
    )

    await handle_emoji_notice(module, event)
    assert bot.calls == [(200, "123")]

    # 同一消息再次上报不重复跟随
    await handle_emoji_notice(module, event)
    assert bot.calls == [(200, "123")]


async def test_handle_emoji_notice_ignores_remove():
    module = _make_module({
        "follow_emoji": True,
        "follow_emoji_prob": 1.0,
        "cooldown_seconds": 0,
    })
    bot = FakeBot()
    event = SimpleNamespace(
        message_id=201,
        emoji_is_add=False,
        emoji_likes=[{"emoji_id": "123", "count": 1}],
        bot=bot,
    )

    await handle_emoji_notice(module, event)
    assert bot.calls == []
