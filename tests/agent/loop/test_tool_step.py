"""共享步骤运行器（`loop/tool_step.py`）的单测。

这是 P1「6 处循环收口为 1 处」的地基：三个流式入口此后都只调它。
所以这里把**循环语义**钉死，后面搬接线时才有对照：

1. 两轮工具 → 恰好 3 次请求；
2. 每轮回填 `assistant(tool_calls) → role:tool` 成对，id 自洽；
3. `on_text` 收到的是**切好句、去过流式痕迹**的句子，顺序不变；
4. `on_round_end` 每轮一次，`rounds_left` 递减，`round_text` 只含本轮文本；
5. 轮数用尽 → `max_rounds`；请求报错 → `error`（且不再继续要工具）；
6. 思考内容只计数、不进正文。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.agent.loop.tool_step import (
    KIND_ASSISTANT,
    KIND_EMPTY,
    KIND_ERROR,
    KIND_MAX_ROUNDS,
    StepRequest,
    accumulate_tool_call,
    run_blocking_step,
    run_stream_step,
)
from app.agent.providers.base import LLMResponse, StreamEvent


class _Invoker:
    """按脚本回放"一次模型响应"，并记录每次收到的 messages 快照。

    `resolve_invoker` 的鸭子类型要求 ``chat`` 与 ``stream`` 同时存在
    （`invoker.py:101`），所以这里两个都提供；本文件只走 ``stream``。
    """

    def __init__(self, script: list[list[StreamEvent]]):
        self.script = list(script)
        self.seen: list[list[dict]] = []
        self.params: list[dict] = []

    async def chat(self, *, runtime, chain, messages, params):  # pragma: no cover - 只满足鸭子类型
        raise AssertionError("本测试只应走流式路径")

    async def stream(self, *, runtime, chain, messages, params):
        self.seen.append([dict(m) for m in messages])
        self.params.append(dict(params))
        for event in self.script.pop(0):
            yield event


def _runtime(invoker) -> SimpleNamespace:
    return SimpleNamespace(bot_id="1", invoker=invoker, config={})


def _text(value: str) -> StreamEvent:
    return StreamEvent(type="text", text=value)


def _tool(name: str, args: str, call_id: str) -> StreamEvent:
    return StreamEvent(
        type="tool_call",
        tool_call={"index": 0, "id": call_id, "function": {"name": name, "arguments": args}},
    )


def _done(reason: str = "stop") -> StreamEvent:
    return StreamEvent(type="done", finish_reason=reason)


async def _executor_factory(log: list[tuple[str, dict]]):
    async def _executor(name, args):
        log.append((name, args))
        return f"{name} 的结果"

    return _executor


def _request(invoker, executor=None, **over) -> StepRequest:
    base = dict(
        runtime=_runtime(invoker),
        chain=[{"provider": "fake", "model": "m"}],
        messages=[{"role": "user", "content": "查一下"}],
        params={"model": "m", "temperature": 0.7, "max_tokens": 64, "tool_executor": executor},
        max_rounds=5,
        tool_ctx=SimpleNamespace(extra={}),
        modalities=None,
    )
    base.update(over)
    return StepRequest(**base)


# ---------- 正常两轮 ----------


async def test_two_tool_rounds_then_answer():
    executed: list[tuple[str, dict]] = []
    executor = await _executor_factory(executed)
    invoker = _Invoker([
        [_text("我先查一下。"), _tool("probe_a", '{"x": 1}', "c0"), _done("tool_calls")],
        [_tool("probe_b", '{"y": 2}', "c1"), _done("tool_calls")],
        [_text("查到了。"), _done()],
    ])
    sentences: list[str] = []

    async def _on_text(text: str) -> None:
        sentences.append(text)

    req = _request(invoker, executor, on_text=_on_text)
    result = await run_stream_step(req)

    assert result.kind == KIND_ASSISTANT
    assert result.rounds == 3
    assert result.text == "我先查一下。查到了。"
    assert sentences == ["我先查一下。", "查到了。"]
    assert executed == [("probe_a", {"x": 1}), ("probe_b", {"y": 2})]

    # 三次请求：原始 1 次 + 每次工具回填各 1 次
    assert len(invoker.seen) == 3
    # 第二次请求尾部：assistant(tool_calls) → role:tool
    assert invoker.seen[1][-2]["role"] == "assistant"
    assert [c["id"] for c in invoker.seen[1][-2]["tool_calls"]] == ["c0"]
    assert invoker.seen[1][-1] == {
        "role": "tool", "tool_call_id": "c0", "content": "probe_a 的结果",
    }
    # 第三次请求保留前一对，并追加新的一对
    assert [c["id"] for c in invoker.seen[2][-2]["tool_calls"]] == ["c1"]
    assert invoker.seen[2][-1]["tool_call_id"] == "c1"
    assert len(result.tool_results) == 2


async def test_tool_executor_never_forwarded_to_provider():
    """循环归本模块：执行器绝不能再传给 provider（否则适配器会重复跑循环）。"""
    executed: list[tuple[str, dict]] = []
    executor = await _executor_factory(executed)
    invoker = _Invoker([[_text("好的。"), _done()]])

    await run_stream_step(_request(invoker, executor))

    assert "tool_executor" not in invoker.params[0]


# ---------- round 边界 ----------


async def test_round_end_reports_round_text_and_remaining_rounds():
    executed: list[tuple[str, dict]] = []
    executor = await _executor_factory(executed)
    invoker = _Invoker([
        [_text("第一轮。"), _tool("probe_a", "{}", "c0"), _done("tool_calls")],
        [_text("第二轮。"), _tool("probe_b", "{}", "c1"), _done("tool_calls")],
        [_text("收尾。"), _done()],
    ])
    rounds: list[tuple[int, int, str]] = []

    async def _on_round_end(summary) -> None:
        rounds.append((summary.index, summary.rounds_left, summary.round_text))

    await run_stream_step(_request(invoker, executor, max_rounds=5, on_round_end=_on_round_end))

    assert rounds == [
        (0, 4, "第一轮。"),
        (1, 3, "第二轮。"),
    ], "round_text 必须只含本轮文本（提醒注入靠它判空）"


# ---------- 结局 ----------


async def test_max_rounds_is_reported_not_raised():
    executed: list[tuple[str, dict]] = []
    executor = await _executor_factory(executed)
    invoker = _Invoker([
        [_tool("probe_a", "{}", "c0"), _done("tool_calls")],
        [_tool("probe_a", "{}", "c1"), _done("tool_calls")],
    ])

    result = await run_stream_step(_request(invoker, executor, max_rounds=2))

    assert result.kind == KIND_MAX_ROUNDS
    assert result.rounds == 2


async def test_stream_error_stops_the_loop():
    executed: list[tuple[str, dict]] = []
    executor = await _executor_factory(executed)
    invoker = _Invoker([
        [_tool("probe_a", "{}", "c0"), _done("tool_calls")],
        [StreamEvent(type="error", text="上游 429")],
    ])

    result = await run_stream_step(_request(invoker, executor))

    assert result.kind == KIND_ERROR
    assert "429" in result.error
    assert len(invoker.seen) == 2, "报错后不得再请求"
    assert executed == [("probe_a", {})]


async def test_reasoning_is_counted_but_never_emitted():
    invoker = _Invoker([
        [StreamEvent(type="reasoning", text="想一想"), _text("好。"), _done()],
    ])
    sentences: list[str] = []

    async def _on_text(text: str) -> None:
        sentences.append(text)

    result = await run_stream_step(_request(invoker, on_text=_on_text))

    assert sentences == ["好。"]
    assert result.reasoning_chars == 3
    assert "想" not in result.text


# ---------- 工具碎片累积 ----------


def test_accumulate_tool_call_joins_fragments_and_tolerates_nulls():
    slots: dict = {}
    accumulate_tool_call(slots, {"index": 0, "id": "c0", "function": {"name": "pro", "arguments": '{"a"'}})
    accumulate_tool_call(slots, {"index": 0, "id": None, "function": {"name": "be_a", "arguments": ": 1}"}})

    assert slots[0]["id"] == "c0"
    assert slots[0]["function"]["name"] == "probe_a"
    assert slots[0]["function"]["arguments"] == '{"a": 1}'


# ==================== 非流式（run_blocking_step）====================


class _ChatInvoker:
    """按脚本回放"一次非流式响应"；``stream`` 只为满足鸭子类型存在。"""

    def __init__(self, script: list[LLMResponse]):
        self.script = list(script)
        self.seen: list[list[dict]] = []
        self.params: list[dict] = []

    async def chat(self, *, runtime, chain, messages, params):
        self.seen.append([dict(m) for m in messages])
        self.params.append(dict(params))
        return self.script.pop(0)

    async def stream(self, *, runtime, chain, messages, params):  # pragma: no cover
        raise AssertionError("本测试只应走非流式路径")
        yield  # pragma: no cover - 让函数成为 async generator


def _calls(*items: tuple[str, str, str]) -> list[dict]:
    return [
        {"id": call_id, "type": "function", "function": {"name": name, "arguments": args}}
        for name, args, call_id in items
    ]


async def test_blocking_two_tool_rounds_then_answer():
    executed: list[tuple[str, dict]] = []
    executor = await _executor_factory(executed)
    invoker = _ChatInvoker([
        LLMResponse(text="", tool_calls=_calls(("probe_a", '{"x": 1}', "c0")), usage={"prompt_tokens": 10}),
        LLMResponse(text="", tool_calls=_calls(("probe_b", '{"y": 2}', "c1")), usage={"prompt_tokens": 20}),
        LLMResponse(text="查到了。", usage={"completion_tokens": 5}),
    ])

    result = await run_blocking_step(_request(invoker, executor))

    assert result.kind == KIND_ASSISTANT
    assert result.text == "查到了。"
    assert result.rounds == 3
    assert executed == [("probe_a", {"x": 1}), ("probe_b", {"y": 2})]
    assert len(invoker.seen) == 3
    assert invoker.seen[1][-1]["tool_call_id"] == "c0"
    assert invoker.seen[2][-2]["tool_calls"][0]["id"] == "c1"
    # 非流式同样不能把执行器继续传给适配器
    assert "tool_executor" not in invoker.params[0]
    # usage 跨轮累加（提示词 10+20、补全 5）
    assert result.usage["prompt_tokens"] == 30
    assert result.usage["completion_tokens"] == 5


async def test_blocking_empty_answer_is_reported_with_reasoning():
    invoker = _ChatInvoker([LLMResponse(text="", reasoning="想了很久")])

    result = await run_blocking_step(_request(invoker, max_rounds=3))

    assert result.kind == KIND_EMPTY
    assert result.reasoning_chars == 4
    assert result.rounds == 1, "没有工具调用就该结束，不该白跑后面的轮次"
    assert len(invoker.seen) == 1


async def test_blocking_max_rounds_is_reported_not_raised():
    executed: list[tuple[str, dict]] = []
    executor = await _executor_factory(executed)
    invoker = _ChatInvoker([
        LLMResponse(text="", tool_calls=_calls(("probe_a", "{}", "c0"))),
        LLMResponse(text="", tool_calls=_calls(("probe_a", "{}", "c1"))),
    ])

    result = await run_blocking_step(_request(invoker, executor, max_rounds=2))

    assert result.kind == KIND_MAX_ROUNDS
    assert result.rounds == 2
    assert len(result.tool_results) == 2, "已执行的工具结果不能丢"
