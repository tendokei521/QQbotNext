"""系统级会话上下文工具测试：get_current_session / get_chat_history。

get_chat_history 取代了旧的 get_session_history：零参数、自动定位当前会话，
本地记录不足时自动补拉 QQ 记录，取不到时给明确路标。
"""

from types import SimpleNamespace

import pytest

from app.llm.session import SessionManager
from app.llm.session_tools import build_session_tools


@pytest.fixture
def llm_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    return tmp_path / "llm"


class _FakeBot:
    """记录 get_msg_history 调用，返回构造好的 OneBot 历史消息。"""

    def __init__(self, messages=None, group_name="测试群"):
        self.calls: list[dict] = []
        self.messages = messages if messages is not None else [
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
        self.group_name = group_name

    async def get_msg_history(self, group_id=0, user_id=0, count=20, reverse_order=False):
        self.calls.append({"group_id": group_id, "user_id": user_id, "count": count})
        return {"status": "ok", "retcode": 0, "data": {"messages": self.messages}}

    async def get_group_info(self, group_id, no_cache=False):
        return {"status": "ok", "data": {"group_name": self.group_name}}


def _ctx(runtime, bot, session_id, *, user_id=None, group_id=None, event=None):
    return SimpleNamespace(
        runtime=runtime,
        bot=bot,
        session_id=session_id,
        event=event,
        user_id=user_id,
        group_id=group_id,
    )


def _tool(runtime, ctx, name):
    return next(t for t in build_session_tools(runtime, ctx) if t.name == name)


async def test_get_current_session_group_info(llm_data_dir):
    runtime = SimpleNamespace(bot_id="bot_group")
    event = SimpleNamespace(
        user_id=10001,
        permission_role="admin",
        role="admin",
        user=SimpleNamespace(user_id=10001, nickname="小明", card="小明明"),
        group=SimpleNamespace(group_id=12345, group_name="测试群"),
    )
    ctx = _ctx(runtime, SimpleNamespace(), "group_12345", user_id=10001, group_id=12345, event=event)

    spec = _tool(runtime, ctx, "get_current_session")
    result = await spec.handler(ctx, {})

    assert "会话类型：群聊" in result
    assert "会话ID：group_12345" in result
    assert "Bot ID：bot_group" in result
    assert "当前用户 QQ：10001" in result
    assert "当前用户群名片：小明明" in result
    assert "当前用户角色：admin" in result
    assert "群号：12345" in result
    assert "群名：测试群" in result


async def test_get_current_session_private(llm_data_dir):
    runtime = SimpleNamespace(bot_id="bot_private")
    ctx = _ctx(
        runtime,
        SimpleNamespace(),
        "private_888",
        user_id=888,
        event=SimpleNamespace(user_id=888, permission_role="owner", role="owner"),
    )

    spec = _tool(runtime, ctx, "get_current_session")
    result = await spec.handler(ctx, {})

    assert "会话类型：私聊" in result
    assert "会话ID：private_888" in result
    assert "对方 QQ：888" in result
    assert "当前用户角色：owner" in result


async def test_session_history_tool_replaced_by_chat_history(llm_data_dir):
    """三个历史工具（本地 + 两个 NapCat）收敛为一个零参数入口。"""
    runtime = SimpleNamespace(bot_id="bot_names", config={})
    ctx = _ctx(runtime, SimpleNamespace(), "group_1", user_id=1, group_id=1)
    names = {t.name for t in build_session_tools(runtime, ctx)}

    assert "get_chat_history" in names
    assert "get_session_history" not in names


async def test_chat_history_returns_local_records(llm_data_dir):
    bot_id = "bot_local"
    manager = SessionManager(bot_id)
    try:
        manager.create_session("group_9", "group", 60)
        manager.add_message("group_9", "user", "你好", user_id="100", nickname="小明")
        manager.add_message("group_9", "assistant", "你好呀")

        runtime = SimpleNamespace(bot_id=bot_id, config={"history_auto_qq_min_local": 20})
        bot = _FakeBot()
        ctx = _ctx(runtime, bot, "group_9", user_id=100, group_id=9)

        result = await _tool(runtime, ctx, "get_chat_history").handler(ctx, {"limit": 10})

        assert "【本地会话记录】" in result
        assert "小明" in result
        assert "你好呀" in result
        # 本地条数（2）< 阈值（20）→ auto 会补拉 QQ
        assert bot.calls and bot.calls[0]["group_id"] == 9
        assert "【QQ 聊天记录】" in result
    finally:
        manager.stop_cleanup()


async def test_chat_history_scope_local_never_touches_qq(llm_data_dir):
    runtime = SimpleNamespace(bot_id="bot_scope_local", config={})
    bot = _FakeBot()
    ctx = _ctx(runtime, bot, "group_5", user_id=1, group_id=5)

    result = await _tool(runtime, ctx, "get_chat_history").handler(ctx, {"scope": "local"})

    assert bot.calls == []
    assert "QQ 记录：未查询" in result or "未查询" in result


async def test_chat_history_auto_skips_qq_when_local_enough(llm_data_dir):
    bot_id = "bot_enough"
    manager = SessionManager(bot_id)
    try:
        manager.create_session("group_7", "group", 60)
        for i in range(6):
            manager.add_message("group_7", "user", f"消息{i}", user_id="100", nickname="小明")

        runtime = SimpleNamespace(bot_id=bot_id, config={"history_auto_qq_min_local": 3})
        bot = _FakeBot()
        ctx = _ctx(runtime, bot, "group_7", user_id=100, group_id=7)

        result = await _tool(runtime, ctx, "get_chat_history").handler(ctx, {})

        assert "本地记录 6 条" in result
        assert bot.calls == []  # 本地充足 → 不补拉，省一次接口调用
    finally:
        manager.stop_cleanup()


async def test_chat_history_private_uses_current_peer(llm_data_dir):
    runtime = SimpleNamespace(bot_id="bot_private_h", config={})
    bot = _FakeBot()
    ctx = _ctx(runtime, bot, "private_888", user_id=888)

    result = await _tool(runtime, ctx, "get_chat_history").handler(
        ctx, {"scope": "qq", "group_id": 999, "user_id": 777}
    )

    # 目标只认当前会话：user_id=888，模型传参被忽略
    assert bot.calls == [{"group_id": 0, "user_id": 888, "count": 30}]
    assert "晚上一起打游戏吗" in result
    assert "【QQ 聊天记录】" in result


async def test_chat_history_empty_gives_next_step_hint(llm_data_dir):
    runtime = SimpleNamespace(bot_id="bot_empty", config={})
    ctx = _ctx(runtime, SimpleNamespace(), "group_404", user_id=1, group_id=404)
    ctx.bot = None

    result = await _tool(runtime, ctx, "get_chat_history").handler(ctx, {})

    assert "本地记录 0 条" in result
    assert "不要假装记得" in result


async def test_chat_history_scope_local_empty_tells_model_to_query_qq(llm_data_dir):
    runtime = SimpleNamespace(bot_id="bot_hint", config={})
    ctx = _ctx(runtime, _FakeBot(), "group_404", user_id=1, group_id=404)

    result = await _tool(runtime, ctx, "get_chat_history").handler(ctx, {"scope": "local"})

    assert "scope=qq" in result
