"""系统级会话上下文工具测试：get_current_session / get_session_history。"""

from types import SimpleNamespace

import pytest

from app.llm.session import SessionManager
from app.llm.session_tools import build_session_tools


@pytest.fixture
def llm_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    return tmp_path / "llm"


async def test_get_current_session_group_info(llm_data_dir):
    runtime = SimpleNamespace(bot_id="bot_group")
    event = SimpleNamespace(
        user_id=10001,
        permission_role="admin",
        role="admin",
        user=SimpleNamespace(user_id=10001, nickname="小明", card="小明明"),
        group=SimpleNamespace(group_id=12345, group_name="测试群"),
    )
    ctx = SimpleNamespace(
        runtime=runtime,
        bot=SimpleNamespace(),
        session_id="group_12345",
        event=event,
        user_id=10001,
        group_id=12345,
    )

    tools = build_session_tools(runtime, ctx)
    spec = next(t for t in tools if t.name == "get_current_session")
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
    ctx = SimpleNamespace(
        runtime=runtime,
        bot=SimpleNamespace(),
        session_id="private_888",
        event=SimpleNamespace(user_id=888, permission_role="owner", role="owner"),
        user_id=888,
        group_id=None,
    )

    tools = build_session_tools(runtime, ctx)
    spec = next(t for t in tools if t.name == "get_current_session")
    result = await spec.handler(ctx, {})

    assert "会话类型：私聊" in result
    assert "会话ID：private_888" in result
    assert "对方 QQ：888" in result
    assert "当前用户角色：owner" in result


async def test_get_session_history(llm_data_dir):
    bot_id = "bot_history"
    manager = SessionManager(bot_id)
    try:
        manager.create_session("group_9", "group", 60)
        manager.add_message(
            "group_9", "user", "你好", user_id="100", nickname="小明"
        )
        manager.add_message("group_9", "assistant", "你好呀")

        runtime = SimpleNamespace(bot_id=bot_id)
        ctx = SimpleNamespace(
            runtime=runtime,
            bot=SimpleNamespace(),
            session_id="group_9",
            event=None,
            user_id=100,
            group_id=9,
        )
        tools = build_session_tools(runtime, ctx)
        spec = next(t for t in tools if t.name == "get_session_history")
        result = await spec.handler(ctx, {"limit": 10})

        assert "最近会话记录：" in result
        assert "小明" in result
        assert "你好呀" in result
    finally:
        manager.stop_cleanup()


async def test_get_session_history_limit_and_empty(llm_data_dir):
    bot_id = "bot_history_empty"
    manager = SessionManager(bot_id)
    try:
        runtime = SimpleNamespace(bot_id=bot_id)
        ctx = SimpleNamespace(
            runtime=runtime,
            bot=SimpleNamespace(),
            session_id="private_empty",
            event=None,
            user_id=1,
            group_id=None,
        )
        tools = build_session_tools(runtime, ctx)
        spec = next(t for t in tools if t.name == "get_session_history")
        result = await spec.handler(ctx, {"limit": 0})

        assert result == "当前会话暂无本地聊天记录。"
    finally:
        manager.stop_cleanup()
