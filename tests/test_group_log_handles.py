"""G 阶段：机器人自己发言的句柄（message_id）回填。

有了句柄才能：① 被引用/被贴表情；② 群聊环境记录里的正文按 message_id 与会话历史去重，
不再同一句话出现两份（一份对话视角、一份环境视角）。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.llm.context import LlmContext, LlmJob
from app.llm.pipeline import LlmPipeline
from app.llm.session import SessionManager


def _manager(bot_id: int = 91001) -> SessionManager:
    """每个测试一个全新实例。

    ``SessionManager`` 按 bot_id 单例（``__new__`` 会查缓存），所以这里绕过它的
    ``__new__`` 直接初始化，避免不同测试互相看到对方的会话。
    """
    mgr = object.__new__(SessionManager)
    mgr._init(bot_id)
    return mgr


def _runtime(session_mgr, bot_id: int = 1):
    return SimpleNamespace(bot_id=bot_id, session_mgr=session_mgr, config={}, proactive=None)


def _ctx(runtime, session_id: str) -> LlmContext:
    event = SimpleNamespace(
        event_type="message_group", message_type="group", message_id=1001,
        group=SimpleNamespace(group_id=1001), user_id=20002,
    )
    return LlmContext(event=event, runtime=runtime, bot=SimpleNamespace(),
                      session_id=session_id, job=LlmJob(id="j1", group_key=session_id))


# ==================== SessionManager 层 ====================


def test_mark_last_assistant_message_id():
    mgr = _manager(91001)
    mgr.create_session("group_91001", "group", timeout=60)
    mgr.add_message("group_91001", "user", "在吗", "20002")
    mgr.add_message("group_91001", "assistant", "在的")

    assert mgr.mark_last_assistant_message_id("group_91001", "556677") is True
    history = mgr.get_history("group_91001")
    assert history[-1]["role"] == "assistant"
    assert history[-1]["message_id"] == "556677"
    # 幂等：同一个 id 不会重复写
    assert mgr.mark_last_assistant_message_id("group_91001", "556677") is False


def test_mark_ignores_missing_targets():
    mgr = _manager(91002)
    assert mgr.mark_last_assistant_message_id("group_91002", "1") is False
    mgr.create_session("group_91002", "group", timeout=60)
    # 历史里没有 assistant（只有 user）→ 不打标，且不报错
    mgr.add_message("group_91002", "user", "只说了这一句", "20002")
    assert mgr.mark_last_assistant_message_id("group_91002", "1") is False
    assert mgr.mark_last_assistant_message_id("group_91002", "") is False


# ==================== Pipeline 层 ====================


async def test_pipeline_send_fills_message_id():
    mgr = _manager(91003)
    runtime = _runtime(mgr, bot_id=91003)
    pipeline = LlmPipeline(runtime)
    ctx = _ctx(runtime, "group_91003")
    mgr.create_session("group_91003", "group", timeout=60)
    mgr.add_message("group_91003", "assistant", "好的，我这就来")

    async def fake_send_group_msg(group_id, message):
        return {"status": "ok", "data": {"message_id": 889900}}

    ctx.bot.send_group_msg = fake_send_group_msg
    await pipeline._send(ctx, SimpleNamespace())

    assert mgr.get_history("group_91003")[-1]["message_id"] == "889900"


async def test_pipeline_send_without_id_does_not_break():
    """响应缺 message_id（部分实现/错误响应）时静默跳过，不影响发送本身。"""
    mgr = _manager(91004)
    runtime = _runtime(mgr, bot_id=91004)
    pipeline = LlmPipeline(runtime)
    ctx = _ctx(runtime, "group_91004")
    mgr.create_session("group_91004", "group", timeout=60)
    mgr.add_message("group_91004", "assistant", "嗯")

    async def fake_send_group_msg(group_id, message):
        return {"status": "failed", "retcode": 100}

    ctx.bot.send_group_msg = fake_send_group_msg
    await pipeline._send(ctx, SimpleNamespace())

    assert "message_id" not in mgr.get_history("group_91004")[-1]


async def test_pipeline_send_without_session_manager_is_safe():
    runtime = SimpleNamespace(bot_id=91005, session_mgr=None, config={}, proactive=None)
    pipeline = LlmPipeline(runtime)
    ctx = _ctx(runtime, "group_91005")

    async def fake_send_group_msg(group_id, message):
        return {"status": "ok", "data": {"message_id": 1}}

    ctx.bot.send_group_msg = fake_send_group_msg
    await pipeline._send(ctx, SimpleNamespace())  # 不抛即通过
