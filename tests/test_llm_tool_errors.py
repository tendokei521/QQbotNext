"""工具执行器的成功/失败判定回归测试。

历史故障：工具处理器用 ``error: ...`` 文本报告失败（不抛异常），而执行器只把
「异常 / 超时 / 无权限」记为失败，于是接口错误（例如 NapCat ``1404 不支持的API``）
在日志与遥测里全部显示成功：

.. code-block:: text

    [ToolCall]返回：get_msg_history success=True ... result=error: ... 1404 不支持的API
"""

from types import SimpleNamespace

from app.llm.hooks import ToolCallHookRegistry
from app.llm.tool import ToolContext, ToolSpec, is_error_result, make_executor


async def _run(handler, *, name="tool_under_test") -> tuple[str, list]:
    """用真实执行器跑一次工具调用，返回 (结果, 捕获到的 ToolCallContext 列表)。"""
    seen: list = []

    async def _capture(call_ctx):
        seen.append(call_ctx)

    registry = ToolCallHookRegistry()
    registry.register(handler=_capture)
    runtime = SimpleNamespace(bot_id="bot_test", config={}, llm_tool_call_hooks=registry)
    spec = ToolSpec(
        name=name,
        description="",
        parameters={"type": "object", "properties": {}},
        handler=handler,
        scopes=("*",),
    )
    result = await make_executor([spec], ToolContext(runtime=runtime))(name, {})
    return result, seen


async def test_error_text_result_marks_tool_call_failed():
    """``error: ...`` 结果必须记为 success=False，错误信息可追溯到遥测。"""

    async def _failing_handler(_ctx, _args):
        return "error: get_msg_history 调用失败: 1404 不支持的API get_msg_history"

    result, seen = await _run(_failing_handler)

    assert result.startswith("error:")
    assert len(seen) == 1
    assert seen[0].success is False
    assert seen[0].extra.get("error") == "error_result"


async def test_normal_text_result_is_still_success():
    """正常文本结果不能被误判为失败。"""

    async def _ok_handler(_ctx, _args):
        return "晴朗，25℃"

    result, seen = await _run(_ok_handler)

    assert result == "晴朗，25℃"
    assert seen[0].success is True
    assert seen[0].extra == {}


async def test_raised_exception_keeps_error_message():
    """抛异常路径的 error 仍保留异常文本，不被新逻辑覆盖。"""

    async def _boom(_ctx, _args):
        raise ValueError("连接已断开")

    result, seen = await _run(_boom)

    assert result.startswith("error:")
    assert seen[0].success is False
    assert "连接已断开" in seen[0].extra.get("error", "")


def test_is_error_result_only_matches_error_prefix():
    assert is_error_result("error: 调用失败")
    assert is_error_result("  error: 调用失败")
    assert not is_error_result("errors: 只是普通文本")
    assert not is_error_result("")
    assert not is_error_result(None)
