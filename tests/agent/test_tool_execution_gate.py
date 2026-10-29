"""执行前复核的回归测试：清单发出后工具被关掉，也不能执行。"""

from __future__ import annotations

from types import SimpleNamespace

from app.agent.tools.spec import ToolSpec


def _runtime(*, disabled: list[str], module_loaded: bool = True):
    config = {"tools_disabled": disabled, "onebot_tools_enable": True}
    cfg = SimpleNamespace(get=lambda k, d=None: config.get(k, d))
    view = SimpleNamespace(get=lambda k, d=None: config.get(k, d)) if module_loaded else None
    return SimpleNamespace(
        bot_id="1",
        config=cfg,
        tools_options=view,
        llm_tools=None,
        mcp_service=None,
        telemetry=None,
        llm_tool_call_hooks=None,
    )


def _spec(calls: list) -> ToolSpec:
    async def _handler(ctx, args):
        calls.append(args)
        return "ok"

    return ToolSpec(
        name="send_poke",
        description="",
        parameters={"type": "object", "properties": {}},
        handler=_handler,
        permission="everyone",
        source="onebot",
    )


async def _run(runtime, spec):
    """走真实的执行入口（make_executor）。"""
    from app.agent.tools.spec import make_executor

    event = SimpleNamespace(event_type="message_group", message_type="group",
                           user_id=1, permission_role="owner")
    ctx = SimpleNamespace(runtime=runtime, event=event, session_id="group_1")
    executor = make_executor([spec], ctx)
    return await executor("send_poke", {})


async def test_blocked_when_user_disabled_after_prompt():
    calls: list = []
    spec = _spec(calls)
    # 清单里已经有它（spec 已构造），但用户在执行前把它关掉了
    out = await _run(_runtime(disabled=["send_poke"]), spec)
    assert out.startswith("error:"), out
    assert "不可用" in out
    assert not calls, "被拦下的工具不该真的执行"


async def test_blocked_when_module_unloaded_after_prompt():
    calls: list = []
    spec = _spec(calls)
    out = await _run(_runtime(disabled=[], module_loaded=False), spec)
    assert "不可用" in out
    assert not calls


async def test_runs_when_everything_is_on():
    calls: list = []
    spec = _spec(calls)
    out = await _run(_runtime(disabled=[]), spec)
    assert out.startswith("ok"), out   # 可能被追加「回应要求」提示，属正常
    assert calls == [{}]
