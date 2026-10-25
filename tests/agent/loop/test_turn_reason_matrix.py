"""一轮对话的**结局矩阵**：五条（现在是六条）路径必须给出六个不同的 `TurnReason`。

## 为什么单独一个文件

改造前"这一轮到底怎么结束的"靠猜日志：兜底话术和真回复在 `text` 上长得一样，
于是记忆蒸馏把兜底话术也蒸馏了、遥测把"想过但没写"记成成功。这里把
`TurnReason` 钉在**真实入口**（`run_turn`）上，而不是只测那个纯函数：

| 路径 | 结局 |
|---|---|
| 正常说完 | `completed` |
| 模型没说话（空回复） | `silent` |
| 工具轮耗尽 | `max_rounds` |
| 流式请求失败 | `error` |
| 被钩子拦下（没请求模型） | `blocked` |
| 思考吃满 max_tokens | `max_tokens` |

取消（`aborted`）在 `test_turn_abort.py`（P2 第二步）里覆盖。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.agent.loop.tool_step import KIND_ASSISTANT, KIND_EMPTY, KIND_ERROR, KIND_MAX_ROUNDS
from app.agent.loop.turn_runner import TurnReason, TurnState, classify_step
from app.agent.providers import PROVIDERS
from app.agent.providers.base import StreamEvent
from app.agent.providers.openai_compat import OpenAICompatProvider
from app.agent.tools.spec import ToolSpec

STREAM_SCRIPT: list[list[StreamEvent]] = []
CHAT_SCRIPT: list[dict] = []


class _ScriptedProvider(OpenAICompatProvider):
    """真实适配器 + 假 HTTP 底座（与契约测试同款替身）。"""

    async def _request(self, payload, timeout):  # noqa: ANN001
        return CHAT_SCRIPT.pop(0)

    async def _stream_once(self, api_key, payload, timeout):  # noqa: ANN001
        for event in STREAM_SCRIPT.pop(0):
            yield event


class _Telemetry:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def record_call_simple(self, **kwargs):
        self.calls.append(kwargs)

    def record_tool(self, *args, **kwargs):
        return None

    def record_hook(self, *args, **kwargs):
        return None


class _Session:
    def mark_replied(self):
        return None


def _config(**over) -> dict:
    base = {
        "history_rounds": 50,
        "max_message_length": 200,
        "max_tool_rounds": 5,
        "empty_reply_retries": 0,
        "reasoning_overflow_retries": 0,
        "stream_output": True,
        "tool_result_directive_enable": False,
    }
    base.update(over)
    return base


def _runtime(config: dict) -> SimpleNamespace:
    return SimpleNamespace(
        bot_id="10001",
        config=config,
        invoker=None,
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


def _specs() -> list[ToolSpec]:
    async def _a(ctx, args):
        return "A 的结果"

    params = {"type": "object", "properties": {}}
    return [ToolSpec(name="probe_a", description="", parameters=params, handler=_a, scopes=("*",))]


def _install(monkeypatch):
    """注册替身 provider，并把 run_turn 的取料/组装换成最小实现。"""
    import app.agent.loop.turn as turn_module

    monkeypatch.setitem(PROVIDERS, "scripted", _ScriptedProvider)
    monkeypatch.setattr(
        turn_module.assembly, "build_for", lambda runtime, req: [{"role": "user", "content": "查一下"}]
    )

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
    return turn_module


def _tool_call(name: str, args: str, call_id: str) -> StreamEvent:
    return StreamEvent(
        type="tool_call",
        tool_call={"index": 0, "id": call_id, "function": {"name": name, "arguments": args}},
    )


async def _run(turn_module, *, stream=True, config=None, user_text="查一下"):
    return await turn_module.run_turn(
        _runtime(config or _config()),
        turn_module.TurnRequest(
            session_id="group_1", is_private=False, user_text=user_text, stream=stream
        ),
    )


# ==================== 矩阵：六条路径 → 六个结局 ====================


async def test_completed_when_the_model_speaks(monkeypatch):
    turn_module = _install(monkeypatch)
    STREAM_SCRIPT.append([StreamEvent(type="text", text="好啊。"), StreamEvent(type="done")])

    result = await _run(turn_module)

    assert result.reason is TurnReason.COMPLETED
    assert result.text == "好啊。"
    assert result.rounds == 1


async def test_silent_when_the_model_says_nothing(monkeypatch):
    turn_module = _install(monkeypatch)
    STREAM_SCRIPT.append([StreamEvent(type="done", finish_reason="stop")])

    result = await _run(turn_module)

    assert result.reason is TurnReason.SILENT
    assert result.blocked is False


async def test_max_rounds_when_the_tool_loop_runs_out(monkeypatch):
    turn_module = _install(monkeypatch)
    STREAM_SCRIPT.extend([
        [_tool_call("probe_a", "{}", "c0"), StreamEvent(type="done", finish_reason="tool_calls")],
        [_tool_call("probe_a", "{}", "c1"), StreamEvent(type="done", finish_reason="tool_calls")],
    ])

    result = await _run(turn_module, config=_config(max_tool_rounds=2))

    assert result.reason is TurnReason.MAX_ROUNDS
    assert result.rounds == 2


async def test_error_when_the_stream_fails(monkeypatch):
    turn_module = _install(monkeypatch)
    STREAM_SCRIPT.append([StreamEvent(type="error", text="上游 429")])

    result = await _run(turn_module)

    assert result.reason is TurnReason.ERROR
    assert result.text == ""


async def test_blocked_when_a_hook_drops_the_request(monkeypatch):
    turn_module = _install(monkeypatch)

    async def _blocked(runtime, ctx, messages, req=None):
        return None

    monkeypatch.setattr(turn_module.assembly, "apply_post_assemble", _blocked)

    result = await _run(turn_module)

    assert result.reason is TurnReason.BLOCKED
    assert result.blocked is True


async def test_max_tokens_when_the_budget_is_eaten_by_thinking(monkeypatch):
    turn_module = _install(monkeypatch)
    CHAT_SCRIPT.append({
        "choices": [{"finish_reason": "length", "message": {"role": "assistant", "content": ""}}],
        "usage": {"completion_tokens": 128},
    })

    result = await _run(turn_module, stream=False)

    assert result.reason is TurnReason.MAX_TOKENS, "被截断 ≠ 模型不想说话"
    # 兜底话术仍然给调用方（用户不能收到空消息），但它不改变结局
    assert "抱歉" in result.text


async def test_reason_reaches_telemetry(monkeypatch):
    turn_module = _install(monkeypatch)
    STREAM_SCRIPT.append([StreamEvent(type="text", text="好啊。"), StreamEvent(type="done")])
    runtime = _runtime(_config())

    await turn_module.run_turn(
        runtime, turn_module.TurnRequest(session_id="group_1", is_private=False, user_text="查一下")
    )

    assert runtime.telemetry.calls[-1]["reason"] == "completed"


# ==================== 判定单元（不经过 run_turn）====================


def _step(kind: str, text: str = "", finish: str = "") -> SimpleNamespace:
    return SimpleNamespace(kind=kind, text=text, finish_reason=finish)


def test_classify_step_prefers_facts_over_text():
    assert classify_step(_step(KIND_ASSISTANT, "你好")) is TurnReason.COMPLETED
    assert classify_step(_step(KIND_EMPTY)) is TurnReason.SILENT
    assert classify_step(_step(KIND_EMPTY, finish="length")) is TurnReason.MAX_TOKENS
    assert classify_step(_step(KIND_ERROR)) is TurnReason.ERROR
    assert classify_step(_step(KIND_MAX_ROUNDS)) is TurnReason.MAX_ROUNDS
    # 有正文时即使被截断也算正常结束（用户已经看到了内容）
    assert classify_step(_step(KIND_ASSISTANT, "半句话", finish="length")) is TurnReason.COMPLETED


def test_turn_state_freezes_terminal_reasons():
    state = TurnState()
    state.add_step(_step(KIND_ERROR))
    assert state.finish() is TurnReason.ERROR
    # 已经落定的结局不会被后来的"看起来正常"覆盖
    state.add_step(_step(KIND_ASSISTANT, "你好"))
    assert state.finish() is TurnReason.ERROR


def test_turn_state_reports_aborted_when_interrupted():
    state = TurnState()
    state.interrupted = True
    state.add_step(_step(KIND_ASSISTANT, "说到一半"))

    assert state.finish() is TurnReason.ABORTED
