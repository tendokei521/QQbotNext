"""工具执行器的成功/失败判定回归测试。

历史故障：工具处理器用 ``error: ...`` 文本报告失败（不抛异常），而执行器只把
「异常 / 超时 / 无权限」记为失败，于是接口错误（例如 OneBot ``1404 不支持的API``）
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
    # 失败结果不追加「回应要求」：此时模型需要的是纠错而不是语气约束
    assert "给你自己看的资料" not in result
    assert len(seen) == 1
    assert seen[0].success is False
    assert seen[0].extra.get("error") == "error_result"


async def test_normal_text_result_is_still_success():
    """正常文本结果不能被误判为失败。

    注意：成功结果末尾会拼上「回应要求」（``tool_loop.DEFAULT_REPLY_DIRECTIVE``），
    因此这里断言前缀而非全等；失败结果不拼（见下一条用例）。
    """

    async def _ok_handler(_ctx, _args):
        return "晴朗，25℃"

    result, seen = await _run(_ok_handler)

    assert result.startswith("晴朗，25℃")
    assert "给你自己看的资料" in result      # 回应要求已附加
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


def test_result_budget_can_be_disabled_per_tool():
    """工具可声明"结果不截断"（max_result=0），不被全局 TOOL_RESULT_MAX 砍掉。"""
    from app.llm.tool import _truncate_result

    long_text = "x" * 5000

    assert _truncate_result(long_text, 0) == long_text        # 不截断
    assert len(_truncate_result(long_text, None)) < 5000      # 用全局默认
    assert _truncate_result(long_text, 100).endswith("已截断)")
    assert _truncate_result("短", 0) == "短"


async def test_executor_honours_tool_budget():
    """执行器按工具自身预算回传（context_tools 的展开工具即用 0=不截断）。"""
    from types import SimpleNamespace

    from app.llm.tool import ToolContext, ToolSpec, make_executor

    payload = "正文内容" * 1000

    async def _handler(_ctx, _args):
        return payload

    spec = ToolSpec(name="big", description="", parameters={"type": "object", "properties": {}},
                    handler=_handler, scopes=("*",), max_result=0)
    runtime = SimpleNamespace(bot_id="1", config={"tool_result_directive_enable": False},
                              telemetry=None, llm_tool_call_hooks=None)
    result = await make_executor([spec], ToolContext(runtime=runtime))("big", {})

    assert payload in result
