"""LLM 请求 token 消耗：usage 归一、跨轮累加、请求结束后 info 一次。

上游报什么就用什么（命中/未命中/输入/输出/合计），一个字都没报时不打日志——
token 数宁缺毋编，否则日志会误导成本判断。
"""

from __future__ import annotations

import pytest

import app.llm
from app.llm.providers import (
    PROVIDERS,
    LLMResponse,
    StreamEvent,
    chat_with_fallback,
    iter_stream_with_fallback,
    log_token_usage,
)
from app.llm.providers.base import (
    cache_hit_miss,
    format_usage,
    has_token_usage,
    merge_usage,
)
from app.llm.providers.openai_compat import OpenAICompatProvider, events_from_chunk


class _FakeLogger:
    """只记录 info 文本的假 logger（真实 logger 是 PrefixLogger 适配器）。"""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def add_info(self, _tag: str) -> _FakeLogger:
        return self

    def info(self, msg: str) -> None:
        self.lines.append(str(msg))

    def warning(self, msg: str) -> None:  # pragma: no cover - 兜底
        self.lines.append(f"W:{msg}")

    def error(self, msg: str) -> None:  # pragma: no cover - 兜底
        self.lines.append(f"E:{msg}")


@pytest.fixture
def fake_logger(monkeypatch) -> _FakeLogger:
    fake = _FakeLogger()
    monkeypatch.setattr(app.llm, "logger", fake, raising=False)
    return fake


# ==================== usage 归一 / 累加 ====================


def test_format_usage_prefers_upstream_cache_fields():
    deepseek = {
        "prompt_tokens": 1234,
        "completion_tokens": 567,
        "total_tokens": 1801,
        "prompt_cache_hit_tokens": 1000,
        "prompt_cache_miss_tokens": 234,
    }
    assert format_usage(deepseek) == (
        "输入 1234（缓存命中 1000 / 未命中 234） / 输出 567 / 合计 1801 tokens"
    )


def test_format_usage_openai_cached_details():
    usage = {"prompt_tokens": 100, "completion_tokens": 20, "prompt_tokens_details": {"cached_tokens": 80}}
    assert format_usage(usage) == "输入 100（缓存命中 80 / 未命中 20） / 输出 20 tokens"


def test_format_usage_anthropic_fields():
    usage = {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_input_tokens": 3,
        "cache_creation_input_tokens": 7,
    }
    assert format_usage(usage) == "输入 10（缓存命中 3 / 未命中 7） / 输出 5 tokens"


def test_format_usage_without_cache_or_total():
    assert format_usage({"prompt_tokens": 12, "completion_tokens": 3}) == "输入 12 / 输出 3 tokens"
    assert format_usage({}) == ""
    assert format_usage(None) == ""


def test_cache_hit_miss_never_invents_numbers():
    """上游没报命中/未命中 → (None, None)，绝不用输入数猜。"""
    assert cache_hit_miss({"prompt_tokens": 100}) == (None, None)
    assert cache_hit_miss(None) == (None, None)


def test_merge_usage_sums_rounds_and_keeps_details():
    total: dict = {}
    merge_usage(total, {"prompt_tokens": 10, "completion_tokens": 4, "prompt_cache_hit_tokens": 8})
    merge_usage(total, {"prompt_tokens": 5, "completion_tokens": 1, "prompt_cache_hit_tokens": 0})
    assert total["prompt_tokens"] == 15
    assert total["completion_tokens"] == 5
    assert total["prompt_cache_hit_tokens"] == 8

    nested: dict = {}
    merge_usage(nested, {"prompt_tokens_details": {"cached_tokens": 3}})
    merge_usage(nested, {"prompt_tokens_details": {"cached_tokens": 4}})
    assert nested["prompt_tokens_details"]["cached_tokens"] == 7


def test_has_token_usage():
    assert has_token_usage({"prompt_tokens": 0})  # 报 0 也算报
    assert has_token_usage({"prompt_cache_hit_tokens": 5})
    assert not has_token_usage({})
    assert not has_token_usage(None)


# ==================== info 一次 ====================


def test_log_token_usage_skips_when_upstream_reported_nothing(fake_logger):
    log_token_usage({}, model="m")
    log_token_usage(None)
    assert fake_logger.lines == []


def test_log_token_usage_writes_one_line(fake_logger):
    log_token_usage(
        {"prompt_tokens": 100, "completion_tokens": 20, "prompt_cache_hit_tokens": 80},
        model="deepseek-chat",
        chars=27,
    )
    assert len(fake_logger.lines) == 1
    line = fake_logger.lines[0]
    assert "本次请求消耗" in line
    assert "缓存命中 80" in line
    assert "回复 27 字符" in line
    assert "model=deepseek-chat" in line


class _UsageProvider:
    """假的 OpenAI 兼容 provider：按预设次数报 usage，供回退/累加测试。"""

    name = "probe_usage"

    def __init__(self, config: dict) -> None:
        self.config = config or {}
        self.calls = 0

    def supports(self, _capability: str) -> bool:
        return False

    async def chat(self, messages, **kwargs) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            text="ok",
            usage={"prompt_tokens": 10, "completion_tokens": 2},
            raw={"choices": []},
        )


async def test_chat_with_fallback_logs_once_with_total(monkeypatch, fake_logger):
    monkeypatch.setitem(PROVIDERS, "probe_usage", _UsageProvider)
    chain = [{"provider": "probe_usage", "model": "probe-model"}]

    resp = await chat_with_fallback(chain, [{"role": "user", "content": "hi"}])

    assert resp.text == "ok"
    assert len(fake_logger.lines) == 1
    line = fake_logger.lines[0]
    assert "输入 10 / 输出 2 tokens" in line
    assert "非流式" in line
    assert "model=probe-model" in line


class _StreamUsageProvider:
    """假的流式 provider：文本 + 末尾 usage 事件。"""

    name = "probe_stream_usage"

    def __init__(self, config: dict) -> None:
        self.config = config or {}

    def supports(self, _capability: str) -> bool:
        return True

    async def chat_stream(self, messages, **kwargs):
        yield StreamEvent(type="text", text="你好")
        yield StreamEvent(type="usage", usage={"prompt_tokens": 7, "completion_tokens": 3})


async def test_stream_usage_sink_accumulates_without_logging(monkeypatch, fake_logger):
    """带 sink（流式工具循环的调用方）时不在这里打日志，只回写汇总。"""
    monkeypatch.setitem(PROVIDERS, "probe_stream_usage", _StreamUsageProvider)
    sink: dict = {}
    events = [
        ev
        async for ev in iter_stream_with_fallback(
            [{"provider": "probe_stream_usage", "model": "m"}],
            [{"role": "user", "content": "hi"}],
            usage_sink=sink,
        )
    ]
    assert [ev.type for ev in events] == ["text", "usage"]
    assert sink == {"prompt_tokens": 7, "completion_tokens": 3}
    assert fake_logger.lines == []

    # 调用方拿着 sink 打一行（多轮再合并就是全程总量）
    log_token_usage(sink, model="m", stream=True)
    assert len(fake_logger.lines) == 1
    assert "流式" in fake_logger.lines[0]


async def test_stream_without_sink_logs_once(monkeypatch, fake_logger):
    monkeypatch.setitem(PROVIDERS, "probe_stream_usage", _StreamUsageProvider)
    events = [
        ev
        async for ev in iter_stream_with_fallback(
            [{"provider": "probe_stream_usage", "model": "m"}],
            [{"role": "user", "content": "hi"}],
        )
    ]
    assert len(events) == 2
    assert len(fake_logger.lines) == 1
    assert "输入 7 / 输出 3 tokens" in fake_logger.lines[0]


# ==================== OpenAI 兼容解析 ====================


def test_events_from_chunk_keeps_usage_only_chunk():
    """开了 include_usage 后，最后一个 chunk 是 choices:[] + usage，不能被丢掉。"""
    events = events_from_chunk({"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 1}})
    assert [ev.type for ev in events] == ["usage"]
    assert events[0].usage["prompt_tokens"] == 5


def test_events_from_chunk_text_and_done():
    events = events_from_chunk({
        "choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1},
    })
    assert [ev.type for ev in events] == ["usage", "text", "done"]
    assert events[1].text == "hi"
    assert events[2].finish_reason == "stop"


async def test_openai_chat_aggregates_usage_across_tool_rounds(monkeypatch):
    """非流式工具循环：多轮 usage 求和后再交给调用方（也修了遥测只记最后一轮的老问题）。"""
    provider = OpenAICompatProvider({"model": "m", "api_key": "k"})
    rounds = [
        {
            "choices": [{"message": {"content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "t", "arguments": "{}"}},
            ]}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10, "prompt_cache_hit_tokens": 60},
        },
        {
            "choices": [{"message": {"content": "done"}}],
            "usage": {"prompt_tokens": 150, "completion_tokens": 20, "prompt_cache_hit_tokens": 100},
        },
    ]

    async def _fake_request(payload, timeout):
        return rounds.pop(0)

    async def _executor(name, args):
        return "tool-result"

    monkeypatch.setattr(provider, "_request", _fake_request)
    resp = await provider.chat(
        [{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "t"}}],
        tool_executor=_executor,
    )
    assert resp.text == "done"
    assert resp.usage["prompt_tokens"] == 250
    assert resp.usage["completion_tokens"] == 30
    assert resp.usage["prompt_cache_hit_tokens"] == 160
