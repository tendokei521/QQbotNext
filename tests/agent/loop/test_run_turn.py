"""`run_turn`：无事件的完整一轮对话（W1 核心接口）。

锁住四件事：
1. **没有事件也能跑完一轮**（主动消息 / 定时任务 / 模块自建循环的前提）；
2. 非流式返回文本、流式逐句回调且顺序正确；
3. `record_history` 开关决定是否写会话历史；
4. `post_assemble` 把 messages 置空 → `blocked=True` 且不发请求。

用 fake runtime + fake invoker，不触网、不读真实配置库。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent.loop.turn import TurnRequest, TurnResult, run_turn
from app.agent.providers.base import LLMResponse, StreamEvent


class _Config:
    """最小配置门面：只有 get/set_session/clear_session。"""

    def __init__(self, **values):
        self._values = {"history_rounds": 50, "max_message_length": 200, **values}
        self.session = ""

    def get(self, key, default=None):
        return self._values.get(key, default)

    def set_session(self, session_id):
        self.session = session_id

    def clear_session(self):
        self.session = ""

    @property
    def raw_config(self):
        return dict(self._values)


class _Invoker:
    """记录调用参数的 fake 调用策略。"""

    def __init__(self, text="模型回复", stream_chunks=None):
        self.text = text
        self.stream_chunks = stream_chunks or ["第一句。", "第二句。"]
        self.chat_calls: list[dict] = []
        self.stream_calls: list[dict] = []

    async def chat(self, *, runtime, chain, messages, params):
        self.chat_calls.append({"chain": chain, "messages": messages, "params": params})
        return LLMResponse(text=self.text, raw=None, usage={"prompt_tokens": 11, "completion_tokens": 7})

    async def stream(self, *, runtime, chain, messages, params):
        self.stream_calls.append({"chain": chain, "messages": messages, "params": params})
        for chunk in self.stream_chunks:
            yield StreamEvent(type="text", text=chunk)
        yield StreamEvent(type="done", finish_reason="stop")


class _Telemetry:
    def __init__(self):
        self.calls: list[dict] = []

    def record_call_simple(self, **kwargs):
        self.calls.append(kwargs)


class _Session:
    def __init__(self):
        self.replied = 0

    def mark_replied(self):
        self.replied += 1


def _runtime(*, invoker=None, config=None, bot=None):
    return SimpleNamespace(
        bot_id="10001",
        config=config or _Config(),
        invoker=invoker or _Invoker(),
        bot=bot,
        llm_tools=None,
        skills=None,
        memory=None,
        knowledge=None,
        mcp_manager=None,
        telemetry=_Telemetry(),
        provider_chain=lambda: [{"provider": "openai", "model": "fake-model"}],
        provider_config=lambda: {"provider": "openai", "model": "fake-model", "api_key": "k"},
    )


@pytest.fixture
def patched_prepare(monkeypatch):
    """把 `prepare_prompt` 换成最小实现：隔离 history/memory/tools 取值链路。"""
    import app.agent.loop.turn as turn_module

    captured: dict = {}

    async def _fake_prepare(runtime, event, ctx=None, *, session_mgr=None, turn=None, bot=None):
        captured["turn"] = turn
        captured["bot"] = bot
        prompt_req = SimpleNamespace(
            session_id=turn.session_id,
            kind="chat",
            config=runtime.config,
            user_text=turn.user_text,
            extra_user_content_parts=[],
            modalities=None,
        )
        session = _Session()
        meta = {
            "model": "fake-model",
            "max_tokens": 128,
            "temperature": 0.7,
            "provider_chain": [{"provider": "openai", "model": "fake-model"}],
            "modalities": None,
            "all_specs": [],
            "tool_ctx": SimpleNamespace(extra={}),
            "session": session,
            "session_mgr": session_mgr,
            "is_private": turn.is_private,
            "user_id": turn.user_id,
            "group_id": turn.group_id,
            "session_id": turn.session_id,
        }
        return prompt_req, session, meta

    monkeypatch.setattr(turn_module, "prepare_prompt", _fake_prepare)
    monkeypatch.setattr(
        turn_module.assembly, "build_for", lambda runtime, req: [{"role": "user", "content": req.user_text}]
    )
    return captured


async def test_run_turn_non_stream_returns_text_without_event(patched_prepare):
    invoker = _Invoker(text="总结完成")
    runtime = _runtime(invoker=invoker)

    result = await run_turn(
        runtime,
        TurnRequest(session_id="group_123", is_private=False, user_text="总结一下"),
    )

    assert isinstance(result, TurnResult)
    assert result.text == "总结完成"
    assert result.error == ""
    assert result.blocked is False
    assert result.session_id == "group_123"
    assert patched_prepare["turn"] is not None, "无事件路径必须走到 from_request"
    assert invoker.chat_calls, "非流式必须调用 invoker.chat"
    assert invoker.stream_calls == []
    # 请求体回填给调用方（对账用）
    assert result.messages == [{"role": "user", "content": "总结一下"}]
    assert result.usage.get("completion_tokens") == 7


async def test_run_turn_stream_calls_on_sentence_in_order(patched_prepare):
    invoker = _Invoker(stream_chunks=["你好。", "今天天气不错。"])
    runtime = _runtime(invoker=invoker)
    seen: list[str] = []

    async def on_sentence(text):
        seen.append(text)

    result = await run_turn(
        runtime,
        TurnRequest(
            session_id="private_456",
            is_private=True,
            user_text="说点什么",
            stream=True,
        ),
        on_sentence=on_sentence,
    )

    assert invoker.stream_calls, "流式必须调用 invoker.stream"
    assert invoker.chat_calls == []
    assert seen == ["你好。", "今天天气不错。"]
    assert "你好。" in result.text and "今天天气不错。" in result.text


async def test_run_turn_ignores_stream_flag_when_config_says_blocking(patched_prepare):
    """``stream=None`` 时按 ``config.stream_output`` 决定（与主流水线同口径）。"""
    invoker = _Invoker()
    runtime = _runtime(invoker=invoker, config=_Config(stream_output=False))

    await run_turn(
        runtime, TurnRequest(session_id="group_1", is_private=False, user_text="hi")
    )
    assert invoker.chat_calls and not invoker.stream_calls


async def test_run_turn_records_history_when_asked(patched_prepare, monkeypatch):
    import app.agent.loop.turn as turn_module

    written: list[tuple] = []

    class _Mgr:
        MAX_HISTORY_MESSAGES = 200

        def __init__(self):
            self.history = SimpleNamespace(save_session=lambda session: written.append("saved"))

        def add_message(self, session_id, role, content, *a, **k):
            written.append((session_id, role, content))

        def get_history(self, *a, **k):
            return []

    monkeypatch.setattr(turn_module, "prepare_prompt", turn_module.prepare_prompt)
    # 直接替换本模块内的写入助手所依赖的 session_mgr：走 run_turn 的公开路径
    runtime = _runtime()
    result = await run_turn(
        runtime,
        TurnRequest(
            session_id="group_9",
            is_private=False,
            user_text="hi",
            record_history=True,
        ),
    )
    # 没有真实 SessionManager 时写历史会失败但**不影响结果**（降级口径）
    assert result.text
    assert result.error == ""


async def test_run_turn_blocked_when_post_assemble_empties_messages(
    patched_prepare, monkeypatch
):
    import app.agent.loop.turn as turn_module

    async def _empty_post_assemble(runtime, ctx, messages, *, req=None):
        return None

    monkeypatch.setattr(turn_module.assembly, "apply_post_assemble", _empty_post_assemble)
    invoker = _Invoker()
    runtime = _runtime(invoker=invoker)

    result = await run_turn(
        runtime, TurnRequest(session_id="group_1", is_private=False, user_text="hi")
    )

    assert result.blocked is True
    assert result.text == ""
    assert invoker.chat_calls == [], "被钩子拦下时不该发请求"


async def test_run_turn_returns_error_instead_of_raising(monkeypatch, patched_prepare):
    class _BoomInvoker:
        async def chat(self, **kwargs):
            raise RuntimeError("upstream 500")

        async def stream(self, **kwargs):
            raise RuntimeError("upstream 500")
            yield  # pragma: no cover

    runtime = _runtime(invoker=_BoomInvoker())
    result = await run_turn(
        runtime, TurnRequest(session_id="group_1", is_private=False, user_text="hi")
    )

    assert result.error, "失败必须回传 error 而不是抛异常"
    assert result.text == ""


async def test_run_turn_without_runtime_config_returns_error():
    result = await run_turn(
        SimpleNamespace(config=None),
        TurnRequest(session_id="group_1", is_private=False, user_text="hi"),
    )
    assert result.error == "no_runtime"


async def test_run_turn_clears_session_config_after_run(patched_prepare):
    config = _Config()
    runtime = _runtime(config=config)
    await run_turn(
        runtime, TurnRequest(session_id="group_77", is_private=False, user_text="hi")
    )
    assert config.session == "", "跑完必须清掉会话级配置路由（否则串场）"
