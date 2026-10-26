"""Q6「工具输出表达力 + 呈现层」的单测。

四件事各钉一组：

1. `ToolResult.plain()` 能渲染**非文本块**（image / ref），且旧构造一个字都不用改；
2. 循环体把本轮所有 `additional_contexts` **汇总成一条** ``role:user`` 消息
   （两个 runner：`run_stream_step` / `run_blocking_step`）；
3. `concludes_turn=True` 让循环**不再发下一次请求**（`StepResult.kind=assistant`，
   工具名进 ``concluded_by``）；
4. 呈现层（`ToolSpec.present_call` / `present_result`）只改**日志文本**，
   不改返回给模型的字符串；渲染器抛异常也不影响调用。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.agent.loop.tool_step import (
    CONTEXT_PREFIX,
    KIND_ASSISTANT,
    StepRequest,
    collect_additional_contexts,
    concluding_tool,
    run_blocking_step,
    run_stream_step,
)
from app.agent.providers.base import LLMResponse, StreamEvent
from app.agent.tools.result import ToolResult
from app.agent.tools.spec import ToolSpec, make_executor

# ==================== 1. plain()：非文本块 ====================


def test_plain_renders_image_block_with_alt():
    tr = ToolResult.image(sha="abc123", mime="image/png", alt="群友发的猫")

    assert tr.plain() == "[图片: 群友发的猫]"
    assert not tr.is_error


def test_plain_renders_image_without_alt_using_sha_and_mime():
    tr = ToolResult.image(sha="abc123", mime="image/png")

    assert tr.plain() == "[图片: abc123/image/png]"


def test_plain_renders_ref_block():
    tr = ToolResult.ref("上一条消息", "msg:42")

    assert tr.plain() == "[引用: 上一条消息 → msg:42]"


def test_plain_keeps_block_order_and_joins_text_without_separator():
    tr = ToolResult(blocks=(
        {"type": "text", "text": "找到了："},
        {"type": "ref", "label": "原帖", "target": "https://example.com/p/1"},
        {"type": "text", "text": "。"},
    ))

    assert tr.plain() == "找到了：[引用: 原帖 → https://example.com/p/1]。"


def test_plain_ignores_unknown_block_types():
    """未知块按老行为忽略（不能因为将来多一种块就把正文弄乱）。"""
    tr = ToolResult(blocks=({"type": "video", "src": "x"}, {"type": "text", "text": "只有这段"}))

    assert tr.plain() == "只有这段"


def test_old_positional_construction_is_untouched():
    """兼容性底线：``(blocks, is_error, code, meta)`` 位置参数原样可用。"""
    tr = ToolResult(({"type": "text", "text": "老代码"},), True, "CODE", {"k": "v"})

    assert tr.is_error and tr.code == "CODE" and tr.meta == {"k": "v"}
    assert tr.additional_contexts == ()
    assert tr.concludes_turn is False
    assert tr.plain() == "老代码"


def test_contexts_are_normalized_to_tuple_and_blank_dropped():
    tr = ToolResult.text("结果", additional_contexts=["  甲  ", "", "乙"])

    assert tr.additional_contexts == ("甲", "乙")


def test_collect_additional_contexts_dedupes_and_keeps_order():
    structured = [
        ToolResult.text("a", additional_contexts=("一", "二")),
        None,  # 拿不到结构化结果（第三方执行器）时是 None
        ToolResult.text("b", additional_contexts=("二", "三")),
    ]

    assert collect_additional_contexts(structured) == ["一", "二", "三"]


def test_concluding_tool_uses_the_call_name_by_index():
    structured = [ToolResult.text("a"), ToolResult.text("b", concludes_turn=True)]
    normalized = [
        {"id": "c0", "function": {"name": "probe_a"}},
        {"id": "c1", "function": {"name": "finish"}},
    ]

    assert concluding_tool(structured, normalized) == "finish"
    assert concluding_tool([ToolResult.text("a")]) == ""


# ==================== 假 invoker / 假执行器 ====================


class _Invoker:
    """流式：按脚本回放一次模型响应，并记录每次请求的 messages 快照。"""

    def __init__(self, script: list[list[StreamEvent]]):
        self.script = list(script)
        self.seen: list[list[dict]] = []

    async def chat(self, *, runtime, chain, messages, params):  # pragma: no cover - 鸭子类型
        raise AssertionError("本测试只应走流式路径")

    async def stream(self, *, runtime, chain, messages, params):
        self.seen.append([dict(m) for m in messages])
        for event in self.script.pop(0):
            yield event


class _ChatInvoker:
    """非流式：按脚本回放一次 ``LLMResponse``。"""

    def __init__(self, script: list[LLMResponse]):
        self.script = list(script)
        self.seen: list[list[dict]] = []

    async def chat(self, *, runtime, chain, messages, params):
        self.seen.append([dict(m) for m in messages])
        return self.script.pop(0)

    async def stream(self, *, runtime, chain, messages, params):  # pragma: no cover
        raise AssertionError("本测试只应走非流式路径")
        yield  # pragma: no cover - 让它成为 async generator


def _runtime(invoker) -> SimpleNamespace:
    return SimpleNamespace(bot_id="1", invoker=invoker, config={})


def _request(invoker, executor=None, **over) -> StepRequest:
    base = dict(
        runtime=_runtime(invoker),
        chain=[{"provider": "fake", "model": "m"}],
        messages=[{"role": "user", "content": "查一下"}],
        params={"model": "m", "tool_executor": executor},
        max_rounds=5,
        tool_ctx=SimpleNamespace(extra={}),
        modalities=None,
    )
    base.update(over)
    return StepRequest(**base)


def _text(value: str) -> StreamEvent:
    return StreamEvent(type="text", text=value)


def _tool(name: str, args: str, call_id: str, *, index: int = 0) -> StreamEvent:
    return StreamEvent(
        type="tool_call",
        tool_call={
            "index": index,
            "id": call_id,
            "function": {"name": name, "arguments": args},
        },
    )


def _done(reason: str = "stop") -> StreamEvent:
    return StreamEvent(type="done", finish_reason=reason)


def _calls(*items: tuple[str, str, str]) -> list[dict]:
    return [
        {"id": call_id, "type": "function", "function": {"name": name, "arguments": args}}
        for name, args, call_id in items
    ]


def _structured_executor(results: dict[str, ToolResult]):
    """生产形状的执行器：返回字符串，同时把 `ToolResult` 放进 ``sink``。

    这正是 `make_executor` 的契约（``sink["result"]`` = 结构化结果），
    循环体靠它拿到 `additional_contexts` / `concludes_turn`。
    """

    async def _executor(name, args, *, sink=None):
        tr = results[name]
        if sink is not None:
            sink["result"] = tr
        return tr.plain()

    return _executor


# ==================== 2. 追加上下文：一条 user 消息 ====================


async def test_stream_runner_appends_contexts_as_one_user_message():
    executor = _structured_executor({
        "probe": ToolResult.text("结果A", additional_contexts=("今天是周一", "无更多记录")),
    })
    invoker = _Invoker([
        [_tool("probe", "{}", "c0"), _done("tool_calls")],
        [_text("查到了。"), _done()],
    ])

    result = await run_stream_step(_request(invoker, executor))

    assert result.kind == KIND_ASSISTANT
    assert len(invoker.seen) == 2
    second = invoker.seen[1]
    # 位置：tool 回执之后、下一次请求之前
    assert [m["role"] for m in second] == ["user", "assistant", "tool", "user"]
    supplement = second[-1]
    assert supplement["content"].startswith(CONTEXT_PREFIX)
    assert "今天是周一" in supplement["content"]
    assert "无更多记录" in supplement["content"]
    assert "今天是周一" in "".join(str(m.get("content") or "") for m in second)


async def test_blocking_runner_appends_contexts_with_the_same_shape():
    executor = _structured_executor({
        "probe": ToolResult.text("结果B", additional_contexts=("补充一条",)),
    })
    invoker = _ChatInvoker([
        LLMResponse(text="", tool_calls=_calls(("probe", "{}", "c0"))),
        LLMResponse(text="好的。"),
    ])

    result = await run_blocking_step(_request(invoker, executor))

    assert result.kind == KIND_ASSISTANT
    assert len(invoker.seen) == 2
    supplement = invoker.seen[1][-1]
    assert supplement["role"] == "user"
    assert supplement["content"] == f"{CONTEXT_PREFIX} 补充一条"


async def test_multiple_contexts_land_in_a_single_message():
    executor = _structured_executor({
        "probe_a": ToolResult.text("a", additional_contexts=("第一条",)),
        "probe_b": ToolResult.text("b", additional_contexts=("第二条",)),
    })
    invoker = _Invoker([
        [_tool("probe_a", "{}", "c0"), _tool("probe_b", "{}", "c1", index=1), _done("tool_calls")],
        [_text("好了。"), _done()],
    ])

    await run_stream_step(_request(invoker, executor))

    supplements = [
        m for m in invoker.seen[1]
        if m["role"] == "user" and str(m.get("content") or "").startswith(CONTEXT_PREFIX)
    ]
    assert len(supplements) == 1, "多条上下文必须汇总成一条消息"
    assert "第一条" in supplements[0]["content"] and "第二条" in supplements[0]["content"]


async def test_no_contexts_means_no_extra_message():
    executor = _structured_executor({"probe": ToolResult.text("普通结果")})
    invoker = _Invoker([
        [_tool("probe", "{}", "c0"), _done("tool_calls")],
        [_text("好了。"), _done()],
    ])

    await run_stream_step(_request(invoker, executor))

    assert invoker.seen[1][-1]["role"] == "tool", "没有追加上下文时消息序列保持原样"


async def test_executor_without_sink_is_tolerated():
    """第三方执行器没有 ``sink``：拿不到结构化结果，但循环照常跑。"""

    async def _plain_executor(name, args):
        return "普通结果"

    invoker = _Invoker([
        [_tool("probe", "{}", "c0"), _done("tool_calls")],
        [_text("好了。"), _done()],
    ])

    result = await run_stream_step(_request(invoker, _plain_executor))

    assert result.kind == KIND_ASSISTANT
    assert len(invoker.seen) == 2


# ==================== 3. concludes_turn：循环停止 ====================


async def test_concludes_turn_stops_the_stream_loop():
    executor = _structured_executor({"finish": ToolResult.text("收尾", concludes_turn=True)})
    invoker = _Invoker([
        [_tool("finish", "{}", "c0"), _done("tool_calls")],
        [_text("不该发生。"), _done()],
    ])

    result = await run_stream_step(_request(invoker, executor))

    assert result.kind == KIND_ASSISTANT
    assert result.concluded_by == "finish"
    assert len(invoker.seen) == 1, "收尾后不得再发请求"
    # tool_calls 必须仍然有配对回执（否则历史下次回放会 400）
    assert result.tool_calls and result.tool_calls[0]["function"]["name"] == "finish"


async def test_concludes_turn_stops_the_blocking_loop():
    executor = _structured_executor({"finish": ToolResult.text("收尾", concludes_turn=True)})
    invoker = _ChatInvoker([
        LLMResponse(text="", tool_calls=_calls(("finish", "{}", "c0"))),
        LLMResponse(text="不该发生。"),
    ])

    result = await run_blocking_step(_request(invoker, executor))

    assert result.kind == KIND_ASSISTANT
    assert result.concluded_by == "finish"
    assert len(invoker.seen) == 1, "收尾后不得再发请求"


async def test_concludes_turn_false_keeps_the_loop_going():
    executor = _structured_executor({"probe": ToolResult.text("还没结束")})
    invoker = _Invoker([
        [_tool("probe", "{}", "c0"), _done("tool_calls")],
        [_text("继续。"), _done()],
    ])

    result = await run_stream_step(_request(invoker, executor))

    assert result.concluded_by == ""
    assert len(invoker.seen) == 2


# ==================== 4. 呈现层 ====================


class _LogSink:
    def __init__(self, lines: list[str], tag: str) -> None:
        self.lines = lines
        self.tag = tag

    def info(self, msg: str) -> None:
        self.lines.append(f"[{self.tag}] {msg}")

    def warning(self, msg: str) -> None:
        self.lines.append(f"[{self.tag}] WARNING {msg}")


class _FakeLogger:
    """最小 logger 替身：只收集 ``add_info(tag).info(...)`` 与直接调用。"""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def add_info(self, tag: str) -> _LogSink:
        return _LogSink(self.lines, tag)

    def info(self, msg: str) -> None:
        self.lines.append(msg)

    def debug(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        self.lines.append(f"WARNING {msg}")


def _ctx() -> SimpleNamespace:
    runtime = SimpleNamespace(bot_id="1", config={}, telemetry=None, llm_tool_call_hooks=None)
    return SimpleNamespace(runtime=runtime, event=None, session_id="group_1", bot=None)


async def _ok(ctx, args):
    return "晴 25℃"


def test_tool_spec_keeps_the_present_fields_and_defaults_them_to_none():
    plain = ToolSpec(name="probe", description="", parameters={}, handler=_ok)
    assert plain.present_call is None and plain.present_result is None

    rendered = ToolSpec(
        name="probe",
        description="",
        parameters={},
        handler=_ok,
        present_call=lambda args: "调用",
        present_result=lambda tr: "结果",
    )
    assert rendered.present_call is not None and rendered.present_result is not None
    assert rendered.present_call({"city": "杭州"}) == "调用"
    assert rendered.present_result(ToolResult.text("x")) == "结果"


async def test_present_layer_renders_into_the_log_lines(monkeypatch):
    fake = _FakeLogger()
    monkeypatch.setattr("app.agent.tools.spec.logger", fake)

    spec = ToolSpec(
        name="weather",
        description="",
        parameters={"type": "object", "properties": {"city": {"type": "string"}}},
        handler=_ok,
        scopes=("*",),
        present_call=lambda args: f"城市={args.get('city', '?')}",
        present_result=lambda tr: f"呈现：{tr.plain()}",
    )
    executor = make_executor([spec], _ctx())

    output = await executor("weather", {"city": "杭州"})

    text = "\n".join(fake.lines)
    assert "城市=杭州" in text, "present_call 必须体现在调用日志里"
    assert "呈现：晴 25℃" in text, "present_result 必须体现在结果日志里"
    # 呈现层只改日志，返回值仍是纯结果字符串（协议面不变）
    assert output.startswith("晴 25℃")


async def test_no_presenter_keeps_the_legacy_log_lines(monkeypatch):
    fake = _FakeLogger()
    monkeypatch.setattr("app.agent.tools.spec.logger", fake)

    spec = ToolSpec(
        name="probe", description="", parameters={}, handler=_ok, scopes=("*",),
    )
    executor = make_executor([spec], _ctx())

    output = await executor("probe", {})

    text = "\n".join(fake.lines)
    assert "[ToolCall]调用工具：probe" in text
    assert "[ToolCall]返回：probe" in text
    assert " | " not in text, "没有呈现层时日志行不该多出分隔段"
    assert output.startswith("晴 25℃")


async def test_broken_presenter_never_breaks_the_call(monkeypatch):
    fake = _FakeLogger()
    monkeypatch.setattr("app.agent.tools.spec.logger", fake)

    def _boom(_value):
        raise RuntimeError("渲染炸了")

    spec = ToolSpec(
        name="probe",
        description="",
        parameters={},
        handler=_ok,
        scopes=("*",),
        present_call=_boom,
        present_result=_boom,
    )
    executor = make_executor([spec], _ctx())

    output = await executor("probe", {})

    assert output.startswith("晴 25℃"), "呈现是日志增强，绝不能影响工具结果"
    text = "\n".join(fake.lines)
    assert "[ToolCall]调用工具：probe" in text, "渲染失败也要留下默认日志行"
