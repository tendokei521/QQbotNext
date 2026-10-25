"""协作式取消（`AbortScope`）：只在**边界**停，停之前发生的事都算数。

为什么必须是协作式而不是 `task.cancel()`：后者会在任意 await 点打断，可能正好
打断"消息已发出去、历史还没写"的窗口，于是用户看到了、模型下一轮却"失忆"。
这里把检查点放在两个明确的位置——**发下一个请求之前**、**派发工具之前**——并且
断言：

1. 取消后不再发请求（不烧钱）；
2. 已经执行的工具不会被"撤回"（它真的跑过了），但没派发的不会再执行；
3. `interrupt_session` 能停住一个正在跑的会话（代际做不到这件事）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent.loop.tool_step import StepRequest, run_blocking_step, run_stream_step
from app.agent.loop.turn_runner import AbortScope, TurnAborted
from app.agent.providers.base import LLMResponse, StreamEvent


class _Invoker:
    """按脚本回放；``chat``/``stream`` 同时存在以满足 `resolve_invoker` 的鸭子类型。"""

    def __init__(self, *, chat_script=None, stream_script=None):
        self.chat_script = list(chat_script or [])
        self.stream_script = list(stream_script or [])
        self.requests = 0

    async def chat(self, *, runtime, chain, messages, params):
        self.requests += 1
        return self.chat_script.pop(0)

    async def stream(self, *, runtime, chain, messages, params):
        self.requests += 1
        for event in self.stream_script.pop(0):
            yield event


def _request(invoker, *, abort=None, executor=None, max_rounds=5) -> StepRequest:
    return StepRequest(
        runtime=SimpleNamespace(bot_id="1", invoker=invoker, config={}),
        chain=[{"provider": "fake", "model": "m"}],
        messages=[{"role": "user", "content": "查一下"}],
        params={"model": "m", "tool_executor": executor},
        max_rounds=max_rounds,
        tool_ctx=SimpleNamespace(extra={}),
        abort=abort,
    )


def test_abort_scope_is_idempotent_and_raises():
    scope = AbortScope()
    assert not scope.aborted
    scope.throw_if_aborted()  # 未取消时不抛

    assert scope.cancel("第一个原因") is True
    assert scope.cancel("第二个原因") is False, "取消是幂等的，第二次不算新取消"
    assert scope.aborted and scope.cause == "第一个原因"
    with pytest.raises(TurnAborted) as info:
        scope.throw_if_aborted()
    assert "第一个原因" in str(info.value)


async def test_blocking_step_does_not_request_when_already_aborted():
    scope = AbortScope()
    scope.cancel("开跑前就没人要了")
    invoker = _Invoker(chat_script=[LLMResponse(text="不该发生")])

    with pytest.raises(TurnAborted):
        await run_blocking_step(_request(invoker, abort=scope))

    assert invoker.requests == 0, "取消后一个请求都不该发"


async def test_streaming_step_stops_before_dispatching_pending_tools():
    """第一轮工具已执行（收不回来），模型又要第二个工具时被取消 → 第二个不派发。"""
    scope = AbortScope()
    executed: list[str] = []

    async def _executor(name, args):
        executed.append(name)
        scope.cancel("工具执行期间来了新消息")   # 在第一个工具里取消
        return "结果"

    invoker = _Invoker(stream_script=[
        [
            StreamEvent(type="tool_call", tool_call={
                "index": 0, "id": "c0", "function": {"name": "first", "arguments": "{}"},
            }),
            StreamEvent(type="done", finish_reason="tool_calls"),
        ],
        # 第二轮不该被请求：上一轮执行完时已取消
        [StreamEvent(type="text", text="不该到这一步"), StreamEvent(type="done")],
    ])

    with pytest.raises(TurnAborted):
        await run_stream_step(_request(invoker, abort=scope, executor=_executor))

    assert executed == ["first"], "已执行的那个工具算数，后面的不派发"
    assert invoker.requests == 1, "取消后不得再发请求"


async def test_abort_before_dispatch_records_a_paired_tool_result():
    """取消时"模型要了但没派发"的工具必须补配对结果，否则 messages 不可回放。"""
    from app.agent.loop.tool_step import ABORTED_BEFORE_DISPATCH

    scope = AbortScope()

    class _LateAbortInvoker(_Invoker):
        async def stream(self, *, runtime, chain, messages, params):
            self.requests += 1
            yield StreamEvent(type="tool_call", tool_call={
                "index": 0, "id": "c0", "function": {"name": "write_thing", "arguments": "{}"},
            })
            # 请求已返回、工具还没派发时被取消
            scope.cancel("写操作不该再执行")

    invoker = _LateAbortInvoker()
    request = _request(invoker, abort=scope, executor=None)

    with pytest.raises(TurnAborted):
        await run_stream_step(request)

    assert invoker.requests == 1
    # 配对：assistant(tool_calls) → role:tool(ABORTED_BEFORE_DISPATCH)
    assert request.messages[-2]["role"] == "assistant"
    assert request.messages[-2]["tool_calls"][0]["id"] == "c0"
    assert request.messages[-1] == {
        "role": "tool",
        "tool_call_id": "c0",
        "content": ABORTED_BEFORE_DISPATCH,
    }


async def test_interrupt_session_cancels_the_running_scope():
    """流水线的 `interrupt_session` 要同时抬代际**和**取消正在跑的循环。"""
    from app.agent.loop.pipeline import LlmPipeline

    runtime = SimpleNamespace(bot_id="1", config={})
    pipeline = LlmPipeline(runtime)
    scope = AbortScope()
    pipeline._active_scopes["group_1"] = scope

    pipeline.interrupt_session("group_1")

    assert scope.aborted, "代际只负责'别再发句子'，停住循环要靠 AbortScope"
    assert pipeline._session_generation["group_1"] == 1
