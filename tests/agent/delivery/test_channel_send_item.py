"""交付通道 `send_item` 测试：会话分流 / 指令解析 / 钩子 / 失败降级。

这些行为此前分散在 5 处重复代码里，且主动消息与定时任务两条路径
**完全没有触发 pre_send/post_send、也不解析 [reply]**。本测试锁住统一后的契约。
"""

from app.agent.delivery.channel import send_item
from app.domain.message import Message


class _FakeBot:
    def __init__(self):
        self.sent: list[tuple[str, int, object]] = []

    async def send_group_msg(self, group_id, message, auto_escape=False):
        self.sent.append(("group", group_id, message))
        return {"status": "ok", "data": {"message_id": 900 + len(self.sent)}}

    async def send_private_msg(self, user_id, message, auto_escape=False):
        self.sent.append(("private", user_id, message))
        return {"status": "ok", "data": {"message_id": 800 + len(self.sent)}}


async def test_send_item_routes_group_and_private():
    bot = _FakeBot()
    await send_item(bot, "group_123", "hi", hooks=False)
    await send_item(bot, "private_456", "hello", hooks=False)

    assert bot.sent[0][0] == "group"
    assert bot.sent[0][1] == 123
    assert bot.sent[0][2].text == "hi"
    assert bot.sent[1][0] == "private"
    assert bot.sent[1][1] == 456


async def test_send_item_returns_message_id():
    bot = _FakeBot()
    resp = await send_item(bot, "group_1", "hi", hooks=False)
    assert (resp or {}).get("data", {}).get("message_id")


async def test_send_item_skips_empty_message():
    bot = _FakeBot()
    assert await send_item(bot, "group_1", "   ", hooks=False) is None
    assert await send_item(bot, "group_1", Message(), hooks=False) is None
    assert bot.sent == []


async def test_send_item_parses_reply_directive():
    """``[reply]`` 会被转成真正的 reply 段（主动/定时路径此前完全不解析）。"""

    class _Event:
        message_id = 555

    bot = _FakeBot()
    await send_item(
        bot, "group_1", "[reply]收到", hooks=False, event=_Event(), parse_directives=True
    )
    seg = bot.sent[0][2]
    assert [s.type for s in seg.segments] == ["reply", "text"]
    assert seg.text == "收到"


async def test_send_item_parses_at_directive_only_in_group():
    class _Event:
        message_id = 555

    bot = _FakeBot()
    await send_item(bot, "group_1", "[@10001]看这个", hooks=False, event=_Event())
    assert [s.type for s in bot.sent[0][2].segments] == ["at", "text"]

    await send_item(bot, "private_2", "[@10001]看这个", hooks=False, event=_Event())
    # 私聊不支持 @：指令被剥离，正文保留
    assert [s.type for s in bot.sent[1][2].segments] == ["text"]
    assert bot.sent[1][2].text == "看这个"


async def test_send_item_can_skip_directive_parsing():
    class _Event:
        message_id = 555

    bot = _FakeBot()
    await send_item(
        bot, "group_1", "[reply]原文", hooks=False, event=_Event(), parse_directives=False
    )
    assert bot.sent[0][2].text == "[reply]原文"


async def test_send_item_without_bot_returns_none():
    assert await send_item(None, "group_1", "hi", hooks=False) is None


async def test_send_item_survives_bot_exception():
    class _Boom:
        async def send_group_msg(self, *a, **k):
            raise RuntimeError("socket closed")

    assert await send_item(_Boom(), "group_1", "hi", hooks=False) is None


async def test_send_item_fires_pre_send_and_post_send_hooks():
    """``hooks=True`` 时触发 LLM 流水线钩子（主动/定时路径此前完全没有）。"""
    calls: list[str] = []

    class _Runtime:
        bot_id = 7
        llm_hooks = None

    async def _fake_run_stage(runtime, stage, ctx, msg=None):
        calls.append(stage)
        return True

    import app.agent.delivery.channel as channel

    original = channel._run_hook
    try:
        async def _patched(runtime, stage, ctx, msg):
            calls.append(stage)
            return True

        channel._run_hook = _patched
        bot = _FakeBot()
        await send_item(bot, "group_1", "hi", runtime=_Runtime(), hooks=True)
    finally:
        channel._run_hook = original

    assert calls == ["pre_send", "post_send"]
    assert len(bot.sent) == 1


async def test_send_item_pre_send_can_block():
    """``pre_send`` 钩子返回 False（置 skip）时消息不发。"""
    class _Runtime:
        bot_id = 7

    import app.agent.delivery.channel as channel

    original = channel._run_hook
    try:
        async def _blocked(runtime, stage, ctx, msg):
            return stage != "pre_send"

        channel._run_hook = _blocked
        bot = _FakeBot()
        assert await send_item(bot, "group_1", "hi", runtime=_Runtime()) is None
        assert bot.sent == []
    finally:
        channel._run_hook = original
