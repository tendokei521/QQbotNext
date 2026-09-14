"""在线历史拉取测试：OneBot 响应信封必须被正确解包。

历史故障：``fetch_group_online_history`` / ``fetch_private_online_history`` 直接读
``result["messages"]``，而真实响应是 ``{"status": "ok", "data": {"messages": [...]}}``，
差一层 → 永远返回空串，「群聊环境背景」/ 主动发言群背景静默失效（日志里成功的
``get_group_msg_history`` 调用与空背景长期共存）。
"""

from types import SimpleNamespace

from app.llm.group_context import (
    extract_history_messages,
    fetch_group_online_history,
    fetch_private_online_history,
)

MESSAGES = [
    {
        "time": 1788342159,
        "sender": {"user_id": 20002, "nickname": "小明", "card": ""},
        "message": [{"type": "text", "data": {"text": "晚上一起打游戏吗"}}],
    },
    {
        "time": 1788342200,
        "sender": {"user_id": 10001, "nickname": "我", "card": ""},
        "message": [{"type": "text", "data": {"text": "好呀"}}],
    },
]


class _FakeBot:
    def __init__(self, response):
        self.response = response
        self.calls: list[dict] = []

    async def get_msg_history(self, group_id=0, user_id=0, count=20, reverse_order=False):
        self.calls.append({"group_id": group_id, "user_id": user_id, "count": count})
        return self.response


def _envelope(messages):
    """OneBot 完整响应信封。"""
    return {"status": "ok", "retcode": 0, "data": {"messages": messages}}


def test_extract_history_messages_handles_both_shapes():
    assert extract_history_messages(_envelope(MESSAGES)) == MESSAGES
    assert extract_history_messages({"messages": MESSAGES}) == MESSAGES
    # 失败响应 / 空响应 / 非法输入
    assert extract_history_messages({"status": "failed", "retcode": 1404, "data": None}) == []
    assert extract_history_messages({"status": "ok", "data": {"messages": []}}) == []
    assert extract_history_messages(None) == []
    assert extract_history_messages("oops") == []


async def test_fetch_group_online_history_unwraps_envelope():
    bot = _FakeBot(_envelope(MESSAGES))

    text = await fetch_group_online_history(bot, group_id=778459818, count=50,
                                            self_ids={"10001"})

    assert "晚上一起打游戏吗" in text
    assert "好呀" in text
    # bot 自己的消息标为「我」
    assert "我: 好呀" in text
    assert bot.calls == [{"group_id": 778459818, "user_id": 0, "count": 50}]


async def test_fetch_private_online_history_unwraps_envelope():
    bot = _FakeBot(_envelope(MESSAGES))

    text = await fetch_private_online_history(bot, user_id=20002, count=20, self_ids={"10001"})

    assert "晚上一起打游戏吗" in text
    assert bot.calls == [{"group_id": 0, "user_id": 20002, "count": 20}]


async def test_fetch_online_history_tolerates_unwrapped_and_failed_responses():
    ok_unwrapped = _FakeBot({"messages": MESSAGES})
    assert "好呀" in await fetch_group_online_history(ok_unwrapped, 1, self_ids=set())

    failed = _FakeBot({"status": "failed", "retcode": 1404, "message": "不支持的API", "data": None})
    assert await fetch_group_online_history(failed, 1) == ""

    disconnected = _FakeBot(None)
    assert await fetch_group_online_history(disconnected, 1) == ""


async def test_group_pre_history_block_reaches_prompt():
    """回归：群聊环境背景块必须真的产出内容（此前恒为空串）。"""
    from app.llm.chat import _build_group_pre_history

    event = SimpleNamespace(
        bot=_FakeBot(_envelope(MESSAGES)),
        self_id=10001,
        bot_id=10001,
        group=SimpleNamespace(group_name="测试群"),
    )

    block = await _build_group_pre_history(event, "778459818", count=50)

    assert "群名：测试群" in block
    assert "最近群聊记录：" in block
    assert "晚上一起打游戏吗" in block
