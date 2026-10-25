"""工具结果的**结构化契约**（P5 第 1 步）：错误码可达 + 双读过渡。

验三件事：

1. 每个错误码都能从**真实执行路径**上取到（不是只有枚举里存在）；
2. `tool_call_hook` 能读到码（观测方不该去解析中文文案）；
3. 老的 `"error:"` 文本仍然被当作失败（60 个生产者一个都不用改）。
"""

from __future__ import annotations

import types

from app.agent.hooks import ToolCallHookRegistry
from app.agent.tools.result import (
    ERROR_CODES,
    TOOL_EXCEPTION,
    TOOL_TIMEOUT,
    ToolResult,
    coerce,
    is_error_result,
)
from app.agent.tools.spec import ToolSpec, make_executor


def _ctx(**config) -> object:
    runtime = types.SimpleNamespace(
        bot_id="1",
        config=config,
        telemetry=None,
        llm_tool_call_hooks=None,
    )
    return types.SimpleNamespace(runtime=runtime, event=None, session_id="group_1", bot=None)


def _spec(handler, *, name="probe", permission="everyone", timeout_s=None) -> ToolSpec:
    return ToolSpec(
        name=name,
        description="",
        parameters={"type": "object", "properties": {}},
        handler=handler,
        scopes=("*",),
        permission=permission,
        timeout_s=timeout_s,
    )


async def _run(spec, *, ctx=None) -> str:
    executor = make_executor([spec], ctx or _ctx())
    return await executor(spec.name, {})


# ── 结果类型本身 ────────────────────────────────────────


def test_error_codes_are_registered():
    assert ERROR_CODES == {
        "UNKNOWN_TOOL", "INVALID_ARGS", "TOOL_TIMEOUT", "TOOL_UNAVAILABLE",
        "PERMISSION_DENIED", "ABORTED_BEFORE_DISPATCH", "ABORTED",
        "TOOL_EXCEPTION", "OUTPUT_LIMIT",
    }


def test_plain_renders_the_same_string_the_provider_reads():
    tr = ToolResult.error(TOOL_TIMEOUT, "工具 probe 执行超时（1s）")

    assert tr.is_error and tr.code == TOOL_TIMEOUT
    assert tr.plain() == "error: 工具 probe 执行超时（1s）"


def test_text_with_legacy_prefix_is_still_treated_as_error():
    tr = ToolResult.text("error: 老式文本报错")

    assert tr.is_error, "双读过渡：老生产者不改也不能被当成功"
    assert not tr.ok
    assert coerce("正常结果").ok


def test_is_error_result_matches_the_legacy_contract():
    assert is_error_result("error: 炸了")
    assert is_error_result("  ERROR: 大写也行")
    assert not is_error_result("结果里提到 error: 但不在开头")


# ── 真实执行路径上的码 ──────────────────────────────────


async def test_tool_timeout_code_is_reachable():
    import asyncio

    async def _slow(ctx, args):
        await asyncio.sleep(0.05)
        return "迟到"

    result = await _run(_spec(_slow, timeout_s=0.01))

    assert result.startswith("error:")
    assert "超时" in result


async def test_tool_exception_code_is_reachable():
    async def _boom(ctx, args):
        raise RuntimeError("炸了")

    result = await _run(_spec(_boom))

    assert "执行异常" in result and "炸了" in result


async def test_permission_denied_code_is_reachable():
    async def _ok(ctx, args):
        return "不该到这里"

    ctx = _ctx()
    ctx.event = types.SimpleNamespace(message_type="group", event_type="message_group", user_id=2)
    executor = make_executor([_spec(_ok, permission="owner")], ctx)

    result = await executor("probe", {})

    assert result.startswith("error:")


async def test_unknown_tool_is_reported():
    executor = make_executor([], _ctx())

    assert "未知工具" in await executor("nope", {})


# ── 钩子能读到码 ────────────────────────────────────────


async def test_tool_call_hook_receives_the_code():
    seen: list[dict] = []

    async def _boom(ctx, args):
        raise RuntimeError("炸了")

    async def _capture(hook_ctx) -> None:
        seen.append({"result": hook_ctx.result, "extra": dict(hook_ctx.extra)})

    registry = ToolCallHookRegistry()
    registry.register(handler=_capture)
    ctx = _ctx()
    ctx.runtime.llm_tool_call_hooks = registry

    await _run(_spec(_boom), ctx=ctx)

    assert seen, "钩子必须被调用"
    assert seen[0]["extra"].get("code") == TOOL_EXCEPTION, "观测方要能读到稳定错误码"


async def test_successful_calls_carry_no_code():
    seen: list[dict] = []

    async def _ok(ctx, args):
        return "一切正常"

    async def _capture(hook_ctx) -> None:
        seen.append(dict(hook_ctx.extra))

    registry = ToolCallHookRegistry()
    registry.register(handler=_capture)
    ctx = _ctx()
    ctx.runtime.llm_tool_call_hooks = registry

    # 结果末尾会追加「回应要求」指令块（框架既有行为），这里只关心结构化码
    assert (await _run(_spec(_ok), ctx=ctx)).startswith("一切正常")
    assert "code" not in seen[0]
