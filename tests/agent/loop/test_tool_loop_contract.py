"""工具循环的**行为契约**：三个入口必须给出同一份可观测行为。

## 为什么要这个文件

工具循环目前有六份实现（`chat.stream_response` / `turn._stream_once` /
`turn._chat_once`→三个 provider 内部 / `proactive.stream`）。历史上反复出现
"流式修了、非流式没修"（`expand_image` 补图轮、补全登记都只在一条路径修过）。

本文件不锁实现位置，只锁**外部可观测行为**：无论循环将来搬到哪一层，
下面这些必须成立 —— 所以它在 P1「循环收口」前后都该是绿的。

## 锁住的不变量

1. 两轮工具 → 恰好 **3 次**模型请求（原始 1 次 + 每次工具回填各 1 次）；
2. 每次回填后的请求尾部严格是 `assistant(tool_calls) → role:tool(...)` 成对；
3. `tool_calls[i].id` 与 `role:tool[i].tool_call_id` 一一对应且非空；
4. 工具执行顺序 == 模型给出的顺序；
5. 最终文本能正常回到调用方。

## 测试替身

`_ScriptedProvider` 继承**真实的** `OpenAICompatProvider`，只替换两个 HTTP 底座
（`_request` / `_stream_once`），因此被测量的是**真实工具循环**，不是假循环。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent.providers import PROVIDERS
from app.agent.providers.base import StreamEvent
from app.agent.providers.openai_compat import OpenAICompatProvider
from app.agent.tools.spec import ToolSpec

#: 每次模型请求实际收到的 messages 快照（按调用顺序）
REQUESTS: list[list[dict]] = []
#: 工具执行记录（按执行顺序）
EXECUTED: list[tuple[str, dict]] = []
#: 流式脚本：每个元素是"一次模型响应"的事件序列
STREAM_SCRIPT: list[list[StreamEvent]] = []
#: 非流式脚本：每个元素是"一次模型响应"的 OpenAI 响应体
CHAT_SCRIPT: list[dict] = []


def _tool_call(name: str, args: str, call_id: str) -> StreamEvent:
    return StreamEvent(
        type="tool_call",
        tool_call={"index": 0, "id": call_id, "function": {"name": name, "arguments": args}},
    )


def _chat_response(name: str, args: str, call_id: str) -> dict:
    return {
        "choices": [{
            "finish_reason": "tool_calls",
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": args},
                }],
            },
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def _chat_text(text: str) -> dict:
    return {
        "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 8},
    }


class _ScriptedProvider(OpenAICompatProvider):
    """真实适配器 + 假 HTTP 底座。"""

    async def _request(self, payload, timeout):  # noqa: ANN001 - 与父类同名同签名
        REQUESTS.append([dict(m) for m in payload["messages"]])
        return CHAT_SCRIPT.pop(0)

    async def _stream_once(self, api_key, payload, timeout):  # noqa: ANN001
        REQUESTS.append([dict(m) for m in payload["messages"]])
        for event in STREAM_SCRIPT.pop(0):
            yield event


@pytest.fixture(autouse=True)
def scripted_provider(monkeypatch):
    """注册替身 provider，并在每个用例前后清空观测缓冲。"""
    REQUESTS.clear()
    EXECUTED.clear()
    STREAM_SCRIPT.clear()
    CHAT_SCRIPT.clear()
    monkeypatch.setitem(PROVIDERS, "scripted", _ScriptedProvider)
    yield


def _specs() -> list[ToolSpec]:
    """两个工具：一个"安全读"、一个"会写"（此处只关心顺序与回填）。"""

    async def _a(ctx, args):
        EXECUTED.append(("probe_a", args))
        return "A 的结果"

    async def _b(ctx, args):
        EXECUTED.append(("probe_b", args))
        return "B 的结果"

    params = {"type": "object", "properties": {}}
    return [
        ToolSpec(name="probe_a", description="", parameters=params, handler=_a, scopes=("*",)),
        ToolSpec(name="probe_b", description="", parameters=params, handler=_b, scopes=("*",)),
    ]


class _Telemetry:
    def record_call_simple(self, **kwargs):
        return None

    def record_tool(self, *args, **kwargs):
        return None

    def record_hook(self, *args, **kwargs):
        return None


class _Session:
    def __init__(self):
        self.replied = 0

    def mark_replied(self):
        self.replied += 1


def _config(**over) -> dict:
    base = {
        "history_rounds": 50,
        "max_message_length": 200,
        "max_tool_rounds": 5,
        "empty_reply_retries": 0,
        "stream_output": True,
        "tool_result_directive_enable": False,
    }
    base.update(over)
    return base


def _runtime(config: dict | None = None, invoker=None):
    return SimpleNamespace(
        bot_id="10001",
        config=config or _config(),
        invoker=invoker,
        bot=None,
        llm_tools=None,
        skills=None,
        memory=None,
        knowledge=None,
        mcp_manager=None,
        telemetry=_Telemetry(),
        provider_config=lambda: {"provider": "scripted", "model": "fake-model", "api_key": "sk-test"},
        provider_chain=lambda: [{"provider": "scripted", "model": "fake-model"}],
        llm_hooks=None,
        llm_tool_call_hooks=None,
    )


def _assert_call_pair(messages: list[dict], call_id: str, result_text: str) -> None:
    """请求尾部必须是一对 ``assistant(tool_calls)`` + ``role:tool``。"""
    assert messages[-2]["role"] == "assistant", messages[-3:]
    tail_calls = messages[-2]["tool_calls"]
    assert [c["id"] for c in tail_calls] == [call_id], tail_calls
    assert messages[-1]["role"] == "tool", messages[-3:]
    assert messages[-1]["tool_call_id"] == call_id
    assert result_text in messages[-1]["content"]


# ==================== 入口一：非流式（run_turn → provider 内部循环）====================


async def test_blocking_turn_runs_two_tool_rounds(monkeypatch):
    import app.agent.loop.turn as turn_module

    CHAT_SCRIPT.extend([
        _chat_response("probe_a", '{"x": 1}', "c0"),
        _chat_response("probe_b", '{"y": 2}', "c1"),
        _chat_text("最终回答"),
    ])

    async def _fake_prepare(runtime, event, ctx=None, *, session_mgr=None, turn=None, bot=None):
        prompt_req = SimpleNamespace(
            session_id=turn.session_id, kind="chat", config=runtime.config,
            user_text=turn.user_text, extra_user_content_parts=[], modalities=None,
        )
        meta = {
            "model": "fake-model", "max_tokens": 128, "temperature": 0.7,
            "provider_chain": [{"provider": "scripted", "model": "fake-model"}],
            "modalities": None, "all_specs": _specs(),
            "tool_ctx": SimpleNamespace(extra={}), "session": _Session(),
            "session_mgr": session_mgr, "is_private": turn.is_private,
            "user_id": turn.user_id, "group_id": turn.group_id,
            "session_id": turn.session_id,
        }
        return prompt_req, _Session(), meta

    monkeypatch.setattr(turn_module, "prepare_prompt", _fake_prepare)
    monkeypatch.setattr(
        turn_module.assembly, "build_for", lambda runtime, req: [{"role": "user", "content": req.user_text}]
    )

    result = await turn_module.run_turn(
        _runtime(),
        turn_module.TurnRequest(session_id="group_1", is_private=False, user_text="查一下", stream=False),
    )

    assert result.error == ""
    assert "最终回答" in result.text
    assert len(REQUESTS) == 3, "两轮工具应当恰好发出三次请求"
    _assert_call_pair(REQUESTS[1], "c0", "A 的结果")
    _assert_call_pair(REQUESTS[2], "c1", "B 的结果")
    assert EXECUTED == [("probe_a", {"x": 1}), ("probe_b", {"y": 2})]


# ==================== 入口二：流式（run_turn → _stream_once）====================


async def test_streaming_turn_runs_two_tool_rounds(monkeypatch):
    import app.agent.loop.turn as turn_module

    STREAM_SCRIPT.extend([
        [_tool_call("probe_a", '{"x": 1}', "c0"), StreamEvent(type="done", finish_reason="tool_calls")],
        [_tool_call("probe_b", '{"y": 2}', "c1"), StreamEvent(type="done", finish_reason="tool_calls")],
        [StreamEvent(type="text", text="最终回答。"), StreamEvent(type="done", finish_reason="stop")],
    ])

    async def _fake_prepare(runtime, event, ctx=None, *, session_mgr=None, turn=None, bot=None):
        prompt_req = SimpleNamespace(
            session_id=turn.session_id, kind="chat", config=runtime.config,
            user_text=turn.user_text, extra_user_content_parts=[], modalities=None,
        )
        meta = {
            "model": "fake-model", "max_tokens": 128, "temperature": 0.7,
            "provider_chain": [{"provider": "scripted", "model": "fake-model"}],
            "modalities": None, "all_specs": _specs(),
            "tool_ctx": SimpleNamespace(extra={}), "session": _Session(),
            "session_mgr": session_mgr, "is_private": turn.is_private,
            "user_id": turn.user_id, "group_id": turn.group_id,
            "session_id": turn.session_id,
        }
        return prompt_req, _Session(), meta

    monkeypatch.setattr(turn_module, "prepare_prompt", _fake_prepare)
    monkeypatch.setattr(
        turn_module.assembly, "build_for", lambda runtime, req: [{"role": "user", "content": req.user_text}]
    )

    sentences: list[str] = []

    async def _on_sentence(text: str) -> None:
        sentences.append(text)

    result = await turn_module.run_turn(
        _runtime(),
        turn_module.TurnRequest(session_id="group_1", is_private=False, user_text="查一下", stream=True),
        on_sentence=_on_sentence,
    )

    assert result.error == ""
    assert sentences == ["最终回答。"], "流式必须逐句回调给调用方"
    assert len(REQUESTS) == 3
    _assert_call_pair(REQUESTS[1], "c0", "A 的结果")
    _assert_call_pair(REQUESTS[2], "c1", "B 的结果")
    assert EXECUTED == [("probe_a", {"x": 1}), ("probe_b", {"y": 2})]


# ==================== 入口三：主动消息 / 定时任务的流式路径 ====================


async def test_initiative_stream_runs_two_tool_rounds():
    from app.agent.tools.spec import build_tools, make_executor
    from app.plugins.llm_proactive.src.stream import stream_send_initiative

    STREAM_SCRIPT.extend([
        [_tool_call("probe_a", '{"x": 1}', "c0"), StreamEvent(type="done", finish_reason="tool_calls")],
        [_tool_call("probe_b", '{"y": 2}', "c1"), StreamEvent(type="done", finish_reason="tool_calls")],
        [StreamEvent(type="text", text="我来开个话题。"), StreamEvent(type="done", finish_reason="stop")],
    ])

    sent: list[str] = []

    class _Bot:
        async def send_group_msg(self, group_id, message):
            sent.append(str(message))

    specs = _specs()
    tool_ctx = SimpleNamespace(extra={}, runtime=None, bot=_Bot(), session_id="group_1", event=None)

    text = await stream_send_initiative(
        _runtime(),
        _Bot(),
        "group_1",
        True,
        "1",
        [{"role": "user", "content": "（你已经很久没说话了）"}],
        tools=build_tools(specs),
        tool_executor=make_executor(specs, tool_ctx),
        tool_ctx=tool_ctx,
        max_tool_rounds=5,
    )

    assert "我来开个话题" in text
    assert len(REQUESTS) == 3
    _assert_call_pair(REQUESTS[1], "c0", "A 的结果")
    _assert_call_pair(REQUESTS[2], "c1", "B 的结果")
    assert EXECUTED == [("probe_a", {"x": 1}), ("probe_b", {"y": 2})]
    assert sent, "主动消息最终必须真的发出去（走消息池）"
