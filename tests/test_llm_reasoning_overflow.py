"""思考吃满 `max_tokens` 时上层的收敛行为，以及 `max_tokens` 到底取哪一层配置。

背景同上（`tests/test_llm_stream_reasoning.py` 文档串里有完整日志）：线上
「输出 8192 = 上限、回复 0 字符」的两种结局——
非流式还能把 reasoning 当产出（`_response_is_empty` 认它，于是 449 字符），
流式则一路零产出、把回退链跑完、最后发兜底话术。

本文件钉住：

1. **收敛轮**：思考吃满预算时追加一次「不要复述思考、直接作答」的提醒，而不是同一个
   prompt 原样重来；且只补一次，不把工具轮数全耗在同一个问题上。
   （实测：该提醒只是尽力而为——`max_tokens=24` 时仍救不回来，`48` 时两版都能出正文，
   思考长度 55→45 字符。所以它是缓解，不是根治。）
2. **max_tokens 溯源**：请求日志里能直接看出有效值与它来自哪一层，不必再翻数据库猜。
"""

from __future__ import annotations

import types

import pytest

from app.llm import chat
from app.llm.providers import PROVIDERS
from app.llm.providers.base import BaseProvider, LLMResponse, StreamEvent

# ==================== 1. max_tokens 来源 ====================


def test_effective_max_tokens_prefers_provider_model_config():
    """链上（provider 模型/预设）写了 max_tokens 就赢过 Agent 配置。"""
    value, src = chat._effective_max_tokens(
        [{"provider": "x", "model": "m", "max_tokens": 8192}], 1024
    )
    assert (value, src) == (8192, "provider 模型/预设")


def test_effective_max_tokens_falls_back_to_agent_config():
    value, src = chat._effective_max_tokens([{"provider": "x", "model": "m"}], 2048)
    assert (value, src) == (2048, "agent 配置")


def test_effective_max_tokens_ignores_blank_chain_value():
    """链上写了空串/None 等于没写，要落到 Agent 配置。"""
    value, src = chat._effective_max_tokens([{"max_tokens": ""}, {"max_tokens": None}], 300)
    assert (value, src) == (300, "agent 配置")


def test_effective_max_tokens_tolerates_garbage():
    assert chat._effective_max_tokens([{"max_tokens": "abc"}], 300) == (300, "agent 配置")
    assert chat._effective_max_tokens(None, None) == (0, "agent 配置")


def test_reasoning_overflow_retries_clamped():
    assert chat._reasoning_overflow_retries({}) == chat.DEFAULT_REASONING_OVERFLOW_RETRIES
    assert chat._reasoning_overflow_retries({"reasoning_overflow_retries": 0}) == 0
    assert chat._reasoning_overflow_retries({"reasoning_overflow_retries": 2}) == 2
    assert chat._reasoning_overflow_retries({"reasoning_overflow_retries": 99}) == 2
    assert chat._reasoning_overflow_retries({"reasoning_overflow_retries": -1}) == 0
    assert chat._reasoning_overflow_retries({"reasoning_overflow_retries": "abc"}) == (
        chat.DEFAULT_REASONING_OVERFLOW_RETRIES
    )


# ==================== 2. 流式收敛轮 ====================

STREAM_MESSAGES: list[list[dict]] = []
STREAM_SCRIPT: list[list[StreamEvent]] = []


class _NudgeProbe(BaseProvider):
    """按脚本产出：第一轮只有思考（预算打满），第二轮才给正文。"""

    async def chat_stream(self, messages, **kwargs):
        STREAM_MESSAGES.append([dict(m) for m in messages])
        index = min(len(STREAM_MESSAGES) - 1, len(STREAM_SCRIPT) - 1)
        for event in STREAM_SCRIPT[index]:
            yield event


class _Skills:
    def prompt_blocks(self):
        return []


class _Tools:
    def enabled_specs(self):
        return []


class _Telemetry:
    def record_call_simple(self, **kwargs):
        return None

    def record_tool(self, *args, **kwargs):
        return None


@pytest.fixture(autouse=True)
def _reset():
    STREAM_MESSAGES.clear()
    STREAM_SCRIPT.clear()
    yield
    STREAM_MESSAGES.clear()
    STREAM_SCRIPT.clear()


def _runtime(bot_id: int, config: dict):
    from app.llm.session import SessionManager

    rt = types.SimpleNamespace()
    rt.bot_id = bot_id
    rt.config = dict(config)
    rt.memory = None
    rt.session_mgr = SessionManager(str(bot_id))
    rt.skills = _Skills()
    rt.llm_tools = _Tools()
    rt.telemetry = _Telemetry()
    rt.provider_config = lambda: {"api_key": "sk-test", "model": "nudge_probe"}
    rt.provider_chain = lambda: [{"provider": "nudge_probe", "model": "nudge_probe"}]
    return rt


def _event():
    from app.domain.events import GroupMessageEvent, MessageSegment, UserInfo

    return GroupMessageEvent(
        event_type="message_group",
        message_type="group",
        time=1788342260,
        user_id=20002,
        self_id=778,
        message=[MessageSegment("text", {"text": "在吗"})],
        user=UserInfo(user_id=20002, nickname="小明"),
        group=types.SimpleNamespace(group_id=466052056, group_name="测试群"),
    )


def _ctx():
    from app.llm.context import LlmContext

    event = _event()
    ctx = LlmContext(event=event, runtime=None, bot=None, session_id="group_466052056")
    ctx.user_text = "在吗"
    ctx.state["user_context"] = {"sent_text": "在吗", "sender": "小明(20002)"}
    ctx.state["is_at"] = True
    return ctx


def _config(**over):
    base = {
        "system_prompt": "你是助手",
        "group_enable": True,
        "private_enable": True,
        "meta_sender_style": "off",
        "history_rounds": 50,
        "schedule_enable": True,
        "stream_output": True,
        "max_tool_rounds": 5,
        "max_tokens": 1024,
    }
    base.update(over)
    return base


async def _run_stream(rt, ctx) -> list[str]:
    return [str(item) async for item in chat.stream_response(rt, ctx.event, ctx)]


async def test_stream_response_nudges_once_after_reasoning_overflow(monkeypatch, tmp_path):
    """思考吃满预算 → 追加一次「直接作答」收敛轮，第二轮正文能发出。"""
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    monkeypatch.setitem(PROVIDERS, "nudge_probe", _NudgeProbe)
    STREAM_SCRIPT.extend([
        [
            StreamEvent(type="reasoning", text="想了很久" * 20),
            StreamEvent(type="done", finish_reason="length"),
        ],
        [
            StreamEvent(type="text", text="直接回答。"),
            StreamEvent(type="done", finish_reason="stop"),
        ],
    ])
    rt = _runtime(1201, _config())
    ctx = _ctx()

    sent = await _run_stream(rt, ctx)

    assert sent == ["直接回答。"], "第二轮正文要真的发出去"
    assert len(STREAM_MESSAGES) == 2, "只补一次收敛轮"
    nudge = STREAM_MESSAGES[1][-1]
    assert nudge["role"] == "user"
    assert "直接给最终回答" in nudge["content"]


async def test_stream_response_nudge_can_be_disabled(monkeypatch, tmp_path):
    """配置 0 = 关闭收敛轮，回到旧行为（给兜底话术，但只请求一次）。"""
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    monkeypatch.setitem(PROVIDERS, "nudge_probe", _NudgeProbe)
    STREAM_SCRIPT.extend([[
        StreamEvent(type="reasoning", text="想了很久"),
        StreamEvent(type="done", finish_reason="length"),
    ]])
    rt = _runtime(1202, _config(reasoning_overflow_retries=0))
    ctx = _ctx()

    sent = await _run_stream(rt, ctx)

    assert len(STREAM_MESSAGES) == 1
    assert sent == ["抱歉，我暂时无法回答，请稍后再试。"]


async def test_stream_response_nudge_only_once(monkeypatch, tmp_path):
    """收敛轮也失败时不再无限补：总轮次有界，最后仍走兜底。"""
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    monkeypatch.setitem(PROVIDERS, "nudge_probe", _NudgeProbe)
    STREAM_SCRIPT.extend([[
        StreamEvent(type="reasoning", text="又想了很久"),
        StreamEvent(type="done", finish_reason="length"),
    ]])
    rt = _runtime(1203, _config())
    ctx = _ctx()

    sent = await _run_stream(rt, ctx)

    assert len(STREAM_MESSAGES) == 2, "思考吃满只补一次，不把轮数全耗在同一问题上"
    assert sent == ["抱歉，我暂时无法回答，请稍后再试。"]


async def test_stream_response_normal_text_still_works(monkeypatch, tmp_path):
    """普通路径不受影响：有正文就直接发，不补轮。"""
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    monkeypatch.setitem(PROVIDERS, "nudge_probe", _NudgeProbe)
    STREAM_SCRIPT.extend([[
        StreamEvent(type="reasoning", text="想一下"),
        StreamEvent(type="text", text="在的。"),
        StreamEvent(type="done", finish_reason="stop"),
    ]])
    rt = _runtime(1204, _config())
    ctx = _ctx()

    sent = await _run_stream(rt, ctx)

    assert sent == ["在的。"]
    assert len(STREAM_MESSAGES) == 1
    assert not [m for m in STREAM_MESSAGES[0] if "直接给最终回答" in str(m.get("content", ""))]


async def test_stream_response_request_log_reports_max_tokens_source(monkeypatch, tmp_path):
    """请求日志直接写出有效 max_tokens 与来源层（排查「8192 哪来的」用）。"""
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    monkeypatch.setitem(PROVIDERS, "nudge_probe", _NudgeProbe)
    STREAM_SCRIPT.extend([[
        StreamEvent(type="text", text="好。"),
        StreamEvent(type="done", finish_reason="stop"),
    ]])
    rt = _runtime(1206, _config())

    fake_lines: list[str] = []

    class _Rec:
        def add_info(self, _tag):
            return self

        def info(self, msg):
            fake_lines.append(str(msg))

        def warning(self, msg):
            fake_lines.append(f"W:{msg}")

        def error(self, msg):
            fake_lines.append(f"E:{msg}")

        def debug(self, msg):
            pass

    import app.llm.chat as chat_mod

    monkeypatch.setattr(chat_mod, "logger", _Rec(), raising=False)

    await _run_stream(rt, _ctx())

    request_lines = [ln for ln in fake_lines if "流式 API 请求" in ln]
    assert len(request_lines) == 1
    assert "max_tokens=1024" in request_lines[0]
    assert "来自agent 配置" in request_lines[0]


# ==================== 3. 非流式同款 ====================


async def test_non_stream_reasoning_overflow_also_nudges(monkeypatch, tmp_path):
    """非流式同款：text 空但有 reasoning → 补一次收敛轮。"""
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    calls: list[list[dict]] = []

    class _ChatProbe(BaseProvider):
        async def chat(self, messages, **kwargs):
            calls.append([dict(m) for m in messages])
            if len(calls) == 1:
                return LLMResponse(text="", reasoning="想了很久", raw={"choices": [{}]})
            return LLMResponse(text="直接回答。", raw={"choices": [{}]})

    monkeypatch.setitem(PROVIDERS, "nudge_probe", _ChatProbe)
    rt = _runtime(1205, _config(stream_output=False))

    text = await chat.generate_response(rt, _event(), _ctx())

    assert text == "直接回答。"
    assert len(calls) == 2
    assert "直接给最终回答" in calls[1][-1]["content"]
