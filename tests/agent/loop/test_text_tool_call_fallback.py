"""正文里的工具调用 → 真正的工具调用（发言通道兜底）契约测试。

## 背景

参与度模式下协议是「正文=思考，``send_message``=发言」，assistant 历史也按该协议渲染成
``[tool_call:send_message]{"messages":"..."}``。线上模型会照抄历史写法、把这一行当正文
输出，于是这一轮变成静默。开关打开后，框架要把正文里的这行还原成真正的工具调用，
**发送/历史/回执/后续轮次与模型自己调用工具完全一致**。

## 锁什么

1. 解析层：能还原历史渲染出的那个格式（与 `SPEECH_TOOL_CALL_MARKER` 同形）；
   坏 JSON / 不认识的工具 / 普通文本一律跳过；
2. 流式与非流式两条 runner 行为一致；
3. 只在「正文=思考 + 开关打开」时生效（文本即回复的通道里正文已经发出，不能重放）；
4. 模型自己已经发了工具调用时，正文里的写法不再补执行（避免重复发送）。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.agent.loop.text_tool_call import extract_text_tool_calls
from app.agent.loop.tool_step import (
    KIND_ASSISTANT,
    StepRequest,
    run_blocking_step,
    run_stream_step,
)
from app.agent.message_assembly.history import SPEECH_TOOL_CALL_MARKER
from app.agent.providers.base import LLMResponse, StreamEvent

REPLY = "唔……用得着的时候就叫我出来，用不着就说没我事了，老师真过分"


def _markup(name: str, arguments: dict) -> str:
    """与 assistant 历史渲染完全同形的正文写法。"""
    return f"[tool_call:{name}]" + json.dumps(arguments, ensure_ascii=False)


# ==================== 解析层 ====================


def test_extract_roundtrips_assistant_history_marker():
    """历史渲染出的格式必须能被还原（两个模块的标记不能各写一份漂移）。"""
    text = f"{SPEECH_TOOL_CALL_MARKER}{json.dumps({'messages': REPLY}, ensure_ascii=False)}"

    calls = extract_text_tool_calls(text, id_prefix="r0")

    assert len(calls) == 1
    assert calls[0]["id"] == "r0_1"
    assert calls[0]["type"] == "function"
    assert calls[0]["function"]["name"] == "send_message"
    assert json.loads(calls[0]["function"]["arguments"]) == {"messages": REPLY}


def test_extract_tolerates_whitespace_and_surrounding_prose():
    text = f"想了一下，还是说一句吧。\n[tool_call : send_message]\n  {json.dumps({'messages': REPLY}, ensure_ascii=False)}\n说完了。"

    calls = extract_text_tool_calls(text)

    assert [call["function"]["name"] for call in calls] == ["send_message"]
    assert json.loads(calls[0]["function"]["arguments"])["messages"] == REPLY


def test_extract_supports_multiple_calls_and_finish():
    text = _markup("send_message", {"messages": ["一", "二"]}) + "\n" + _markup("finish", {"summary": "说完了"})

    calls = extract_text_tool_calls(text)

    assert [call["function"]["name"] for call in calls] == ["send_message", "finish"]
    assert [call["id"] for call in calls] == ["text_call_1", "text_call_2"]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "普通正文里没有工具调用。",
        '[tool_call:send_message]{"messages": ',          # JSON 不完整
        '[tool_call:send_message]不是对象',                # 标记后不是 JSON 对象
        '[tool_call:delete_everything]{"x": 1}',          # 不在发言通道白名单里
        "[tool_call:send_message]",                       # 只有标记
    ],
)
def test_extract_skips_non_matching_text(text: str):
    assert extract_text_tool_calls(text) == []


# ==================== 非流式 runner ====================


class _ChatInvoker:
    """按脚本回放非流式响应。"""

    def __init__(self, script: list[LLMResponse]):
        self.script = list(script)
        self.seen: list[list[dict]] = []

    async def chat(self, *, runtime, chain, messages, params):
        self.seen.append([dict(m) for m in messages])
        return self.script.pop(0)

    async def stream(self, *, runtime, chain, messages, params):  # pragma: no cover
        raise AssertionError("本测试只应走非流式路径")
        yield  # pragma: no cover


class _StreamInvoker:
    """按脚本回放流式事件。"""

    def __init__(self, script: list[list[StreamEvent]]):
        self.script = list(script)
        self.seen: list[list[dict]] = []

    async def chat(self, *, runtime, chain, messages, params):  # pragma: no cover
        raise AssertionError("本测试只应走流式路径")

    async def stream(self, *, runtime, chain, messages, params):
        self.seen.append([dict(m) for m in messages])
        for event in self.script.pop(0):
            yield event


def _runtime(invoker) -> SimpleNamespace:
    return SimpleNamespace(bot_id="1", invoker=invoker, config={})


def _request(invoker, executor, *, text_as_thinking: bool, fallback: bool, **over) -> StepRequest:
    base = dict(
        runtime=_runtime(invoker),
        chain=[{"provider": "fake", "model": "m"}],
        messages=[{"role": "user", "content": "在吗"}],
        params={"model": "m", "temperature": 0.7, "max_tokens": 64, "tool_executor": executor},
        max_rounds=5,
        tool_ctx=SimpleNamespace(extra={}),
        modalities=None,
        text_as_thinking=text_as_thinking,
        text_tool_call_fallback=fallback,
    )
    base.update(over)
    return StepRequest(**base)


def _executor(log: list[tuple[str, dict]]):
    async def _exec(name, args):
        log.append((name, args))
        return "ok: 已发出"

    return _exec


async def test_blocking_fallback_executes_text_markup():
    executed: list[tuple[str, dict]] = []
    invoker = _ChatInvoker([
        LLMResponse(text=_markup("send_message", {"messages": REPLY})),
        LLMResponse(text="说完了。"),
    ])

    result = await run_blocking_step(
        _request(invoker, _executor(executed), text_as_thinking=True, fallback=True)
    )

    assert executed == [("send_message", {"messages": REPLY})]
    assert result.kind == KIND_ASSISTANT
    assert result.rounds == 2, "还原出的调用要真的进下一次请求（拿回执后模型再收尾）"
    assert result.text == "说完了。"
    # 下一次请求里必须有成对的 assistant(tool_calls) → role:tool
    assert invoker.seen[1][-2]["role"] == "assistant"
    assert invoker.seen[1][-2]["tool_calls"][0]["function"]["name"] == "send_message"
    assert invoker.seen[1][-1]["role"] == "tool"


async def test_blocking_fallback_off_keeps_markup_as_plain_text():
    executed: list[tuple[str, dict]] = []
    invoker = _ChatInvoker([LLMResponse(text=_markup("send_message", {"messages": REPLY}))])

    result = await run_blocking_step(
        _request(invoker, _executor(executed), text_as_thinking=True, fallback=False)
    )

    assert executed == []
    assert result.text == _markup("send_message", {"messages": REPLY})
    assert result.rounds == 1


async def test_fallback_requires_thinking_channel():
    """文本即回复的通道里正文已经发出去了，不能再按工具执行一次（否则重复发送）。"""
    executed: list[tuple[str, dict]] = []
    invoker = _ChatInvoker([LLMResponse(text=_markup("send_message", {"messages": REPLY}))])

    result = await run_blocking_step(
        _request(invoker, _executor(executed), text_as_thinking=False, fallback=True)
    )

    assert executed == []
    assert result.rounds == 1


async def test_real_tool_calls_take_precedence_over_text_markup():
    """模型自己已经调了工具时不再补执行正文里的写法，避免同一句发两遍。"""
    executed: list[tuple[str, dict]] = []
    invoker = _ChatInvoker([
        LLMResponse(
            text=_markup("send_message", {"messages": "正文里的重复写法"}),
            tool_calls=[{
                "id": "c0",
                "type": "function",
                "function": {"name": "send_message", "arguments": json.dumps({"messages": "真正的调用"})},
            }],
        ),
        LLMResponse(text="说完了。"),
    ])

    await run_blocking_step(
        _request(invoker, _executor(executed), text_as_thinking=True, fallback=True)
    )

    assert executed == [("send_message", {"messages": "真正的调用"})]


# ==================== 保险：本回合已发过言就不再解析正文 ====================


async def test_fallback_skipped_after_real_send_message_this_turn():
    """本回合（跨轮）已经真正调过 send_message，后续轮次的正文写法一律不再补执行。"""
    executed: list[tuple[str, dict]] = []
    invoker = _ChatInvoker([
        LLMResponse(
            text="",
            tool_calls=[{
                "id": "c0",
                "type": "function",
                "function": {"name": "send_message", "arguments": json.dumps({"messages": "先说一句"})},
            }],
        ),
        LLMResponse(text=_markup("send_message", {"messages": "照抄历史又写了一遍"})),
    ])

    result = await run_blocking_step(
        _request(invoker, _executor(executed), text_as_thinking=True, fallback=True)
    )

    assert executed == [("send_message", {"messages": "先说一句"})]
    assert result.send_message_called is True
    assert result.rounds == 2


async def test_fallback_skipped_after_fallback_sent_something_this_turn():
    """兜底还原出的发送同样算"本回合发过言"，下一轮正文不再被解析第二遍。"""
    executed: list[tuple[str, dict]] = []
    invoker = _ChatInvoker([
        LLMResponse(text=_markup("send_message", {"messages": "第一遍"})),
        LLMResponse(text=_markup("send_message", {"messages": "第二遍"})),
    ])

    result = await run_blocking_step(
        _request(invoker, _executor(executed), text_as_thinking=True, fallback=True)
    )

    assert executed == [("send_message", {"messages": "第一遍"})]
    assert result.send_message_called is True
    assert result.rounds == 2, "第二次正文不再触发执行，也不该再多跑一轮请求"


# ==================== 流式 runner ====================


async def test_stream_fallback_executes_text_markup():
    executed: list[tuple[str, dict]] = []
    # 流式按句子切分：标记与 JSON 可能被拆成多段，拼回来仍要能解析
    invoker = _StreamInvoker([
        [
            StreamEvent(type="text", text="我先说一句。"),
            StreamEvent(type="text", text=_markup("send_message", {"messages": REPLY})),
            StreamEvent(type="done", finish_reason="stop"),
        ],
        [StreamEvent(type="text", text="说完了。"), StreamEvent(type="done", finish_reason="stop")],
    ])

    result = await run_stream_step(
        _request(invoker, _executor(executed), text_as_thinking=True, fallback=True)
    )

    assert executed == [("send_message", {"messages": REPLY})]
    assert result.kind == KIND_ASSISTANT
    assert result.rounds == 2
    assert invoker.seen[1][-1]["role"] == "tool"


async def test_stream_fallback_off_keeps_silent_semantics():
    executed: list[tuple[str, dict]] = []
    invoker = _StreamInvoker([
        [StreamEvent(type="text", text=_markup("send_message", {"messages": REPLY})), StreamEvent(type="done")],
    ])

    result = await run_stream_step(
        _request(invoker, _executor(executed), text_as_thinking=True, fallback=False)
    )

    assert executed == []
    assert result.rounds == 1
    assert result.text == _markup("send_message", {"messages": REPLY})


async def test_stream_fallback_skipped_after_send_message_this_turn():
    """流式路径同一保险：本回合发过言后，后续轮次的正文写法不再补执行。"""
    executed: list[tuple[str, dict]] = []
    invoker = _StreamInvoker([
        [
            StreamEvent(
                type="tool_call",
                tool_call={
                    "index": 0,
                    "id": "c0",
                    "function": {
                        "name": "send_message",
                        "arguments": json.dumps({"messages": "先说一句"}),
                    },
                },
            ),
            StreamEvent(type="done", finish_reason="tool_calls"),
        ],
        [StreamEvent(type="text", text=_markup("send_message", {"messages": "又写了一遍"})), StreamEvent(type="done")],
    ])

    result = await run_stream_step(
        _request(invoker, _executor(executed), text_as_thinking=True, fallback=True)
    )

    assert executed == [("send_message", {"messages": "先说一句"})]
    assert result.send_message_called is True
    assert result.rounds == 2
