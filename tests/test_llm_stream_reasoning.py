"""流式「思考流」必须被解析出来，而不是被当成零产出。

线上案例（2026-10-18 22:46，`1/log/qqbot-logs-20261018145731/`）：
同一份 text+image 请求，非流式正常（回复 449 字符），流式却「回复 0 字符」并触发兜底话术：

    22:46:17  流式 API 请求 -> private_1901691195, 消息数: 58
    22:46:27  空回复重试 [1/1]：模型 44a05b80a060 流式无产出
    22:46:57  空回复重试 [1/1]：模型 7b7ddac5428c 流式无产出
    22:47:23  空回复重试 [1/1]：模型 df4c81f3f5e3 流式无产出
    22:47:35  本次请求消耗: 输入 167048 / 输出 8192 / 回复 0 字符 | model=deepseek-v4-flash

根因：思考模型在流式里只发 `delta.reasoning_content`（`delta.content` 恒为 null），
旧解析只认 `content` → 整条流对上层等于「零产出」；而输出 8192 正好等于 max_tokens 上限，
说明预算全花在思考里、正文还没开始就被截断。

本文件只钉**解析层**两件事：

1. `reasoning_content` 必须单独成事件（不能再被丢掉），且绝不能混成回复正文；
2. 思考算「已产出」→ 不再对同一条 prompt 原样重试 / 跑完整条回退链
   （同 prompt + 同 max_tokens 必然再打满一次，线上为此白烧了 17.5 万 token）。

上层怎么处理（收敛轮）见 `tests/test_llm_reasoning_overflow.py`。
"""

from __future__ import annotations

import pytest

from app.agent.providers import PROVIDERS, iter_stream_with_fallback
from app.agent.providers.base import BaseProvider, StreamEvent
from app.agent.providers.openai_compat import events_from_chunk

# ==================== 1. 流式解析：思考流不能丢 ====================


def test_reasoning_chunk_becomes_reasoning_event():
    """思考增量必须单独成事件（旧实现返回 []，整条流被当成零产出）。"""
    chunk = {"choices": [{"delta": {"reasoning_content": "让我先看看这张图……"}}]}
    events = events_from_chunk(chunk)

    assert [ev.type for ev in events] == ["reasoning"]
    assert events[0].text == "让我先看看这张图……"


def test_reasoning_alias_field_supported():
    """部分中转商把字段写成 reasoning。"""
    events = events_from_chunk({"choices": [{"delta": {"reasoning": "想一想"}}]})
    assert [ev.type for ev in events] == ["reasoning"]


def test_reasoning_chunk_produces_no_text_event():
    """思考不能被当成回复正文（否则内心独白会直接发到群里）。"""
    events = events_from_chunk({"choices": [{"delta": {"reasoning_content": "内心戏"}}]})
    assert not [ev for ev in events if ev.type == "text"]


def test_reasoning_and_content_in_same_chunk_keep_order():
    events = events_from_chunk(
        {"choices": [{"delta": {"reasoning_content": "想", "content": "答"}}]}
    )
    assert [(ev.type, ev.text) for ev in events] == [("reasoning", "想"), ("text", "答")]


def test_plain_text_chunk_is_unchanged():
    """普通模型（deepseek-chat 这类）行为逐字不变。"""
    events = events_from_chunk(
        {
            "choices": [{"delta": {"content": "你好"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5},
        }
    )
    assert [ev.type for ev in events] == ["usage", "text", "done"]


# ==================== 2. 思考算产出：不原样重试 ====================

_CALLS: dict[str, int] = {}


class _FakeLogger:
    """只记录文本的假 logger（真实 logger 是 PrefixLogger 适配器）。"""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def add_info(self, _tag: str) -> _FakeLogger:
        return self

    def info(self, msg: str) -> None:
        self.lines.append(str(msg))

    def warning(self, msg: str) -> None:
        self.lines.append(f"W:{msg}")

    def error(self, msg: str) -> None:
        self.lines.append(f"E:{msg}")

    def debug(self, msg: str) -> None:
        self.lines.append(f"D:{msg}")


@pytest.fixture
def fake_logger(monkeypatch) -> _FakeLogger:
    import app.agent

    fake = _FakeLogger()
    monkeypatch.setattr(app.agent, "logger", fake, raising=False)
    return fake


class _ReasoningOnlyStream(BaseProvider):
    """只产出思考、正文一个字节都没有（模拟预算全烧在 reasoning 里）。"""

    async def chat_stream(self, messages, **kwargs):
        name = self.config.get("provider")
        _CALLS[name] = _CALLS.get(name, 0) + 1
        yield StreamEvent(type="reasoning", text="想了很久")
        yield StreamEvent(type="done", finish_reason="length")
        yield StreamEvent(type="usage", usage={"prompt_tokens": 100, "completion_tokens": 8192})


def _register(monkeypatch, name: str) -> dict:
    _CALLS[name] = 0
    monkeypatch.setitem(PROVIDERS, name, _ReasoningOnlyStream)
    return {"provider": name, "model": f"{name}-model"}


async def _drain(chain, **kwargs):
    return [
        ev
        async for ev in iter_stream_with_fallback(
            chain, [{"role": "user", "content": "看图"}], **kwargs
        )
    ]


async def test_reasoning_only_does_not_trigger_empty_reply_retry(monkeypatch, tmp_path):
    """思考已产出 → 不再同 provider 重试（线上为此白烧了 17.5 万 token）。"""
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    cfg = _register(monkeypatch, "reasoning_only_probe")

    events = await _drain([cfg], max_empty_retries=1, empty_retry_backoff=0)

    assert _CALLS["reasoning_only_probe"] == 1, "思考算产出，不该原样重试"
    assert [ev.type for ev in events] == ["reasoning", "done", "usage"]


async def test_reasoning_only_does_not_fall_through_to_next_provider(monkeypatch, tmp_path):
    """同理也不该把整条回退链跑一遍（每个 backup 都会同样打满）。"""
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    primary = _register(monkeypatch, "reasoning_only_primary")
    backup = _register(monkeypatch, "reasoning_only_backup")

    await _drain([primary, backup], max_empty_retries=1, empty_retry_backoff=0)

    assert _CALLS["reasoning_only_primary"] == 1
    assert _CALLS["reasoning_only_backup"] == 0


async def test_reasoning_does_not_count_as_replied_chars(monkeypatch, fake_logger, tmp_path):
    """思考不算「回复 N 字符」：别再出现「输出 8192 / 回复 0 字符」时看不出原因。"""
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    cfg = _register(monkeypatch, "reasoning_only_chars")

    await _drain([cfg], max_empty_retries=0)

    usage_lines = [line for line in fake_logger.lines if "本次请求消耗" in line]
    assert len(usage_lines) == 1
    assert "回复" not in usage_lines[0], "没有正文就不要再报回复字数"
