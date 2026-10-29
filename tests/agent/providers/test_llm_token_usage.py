"""LLM 请求 token 消耗：usage 归一、跨轮累加、请求结束后 info 一次。

上游报什么就用什么（命中/未命中/输入/输出/合计），一个字都没报时不打日志——
token 数宁缺毋编，否则日志会误导成本判断。
"""

from __future__ import annotations

import pytest

import app.agent
from app.agent.providers import (
    PROVIDERS,
    SERVED_MODEL_KEY,
    LLMResponse,
    StreamEvent,
    chat_with_fallback,
    iter_stream_with_fallback,
    log_token_usage,
)
from app.agent.providers.base import (
    cache_hit_miss,
    format_usage,
    has_token_usage,
    merge_usage,
)
from app.agent.providers.openai_compat import OpenAICompatProvider, events_from_chunk


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
    monkeypatch.setattr(app.agent, "logger", fake, raising=False)
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
    text = format_usage(deepseek)
    assert "输入 1234" in text
    assert "缓存命中 1000" in text and "未命中 234" in text
    assert "输出 567" in text and "合计 1801" in text


def test_format_usage_openai_cached_details():
    usage = {"prompt_tokens": 100, "completion_tokens": 20, "prompt_tokens_details": {"cached_tokens": 80}}
    text = format_usage(usage)
    assert "输入 100" in text and "缓存命中 80" in text and "未命中 20" in text
    assert "输出 20" in text


def test_format_usage_anthropic_fields():
    usage = {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_input_tokens": 3,
        "cache_creation_input_tokens": 7,
    }
    text = format_usage(usage)
    assert "输入 10" in text and "缓存命中 3" in text and "未命中 7" in text
    assert "输出 5" in text


def test_format_usage_without_cache_or_total():
    text = format_usage({"prompt_tokens": 12, "completion_tokens": 3})
    assert "输入 12" in text and "输出 3" in text
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


class _FailingUsageProvider:
    """主模型：请求直接异常（非流式回退场景）。"""

    name = "probe_failing_usage"

    def __init__(self, config: dict) -> None:
        self.config = config or {}

    def supports(self, _capability: str) -> bool:
        return False

    async def chat(self, messages, **kwargs) -> LLMResponse:
        raise RuntimeError("boom")


async def test_chat_fallback_logs_served_model_not_primary(monkeypatch, fake_logger):
    """回退到 backup 后，token 消耗那一行必须写 backup 的模型名。

    历史 bug：日志固定取回退链第一个配置（``chain[0]``）的名字，于是 backup 的
    消耗被记在主模型头上（日志里出现「model=主模型」但实际是回退模型在服务）。
    """
    monkeypatch.setitem(PROVIDERS, "probe_failing_usage", _FailingUsageProvider)
    monkeypatch.setitem(PROVIDERS, "probe_usage", _UsageProvider)
    chain = [
        {"provider": "probe_failing_usage", "model": "primary-model"},
        {"provider": "probe_usage", "model": "backup-model"},
    ]

    resp = await chat_with_fallback(chain, [{"role": "user", "content": "hi"}])

    assert resp.text == "ok"
    usage_lines = [line for line in fake_logger.lines if "本次请求消耗" in line]
    assert len(usage_lines) == 1
    assert "model=backup-model" in usage_lines[0]
    assert "primary-model" not in usage_lines[0]


async def test_chat_fallback_uses_last_attempted_model_when_all_fail(monkeypatch, fake_logger):
    """全部 provider 失败：没有「实际服务者」，写最后一个尝试过的模型名。"""

    class _AllFail(_FailingUsageProvider):
        async def chat(self, messages, **kwargs) -> LLMResponse:
            return LLMResponse(text="", raw=None, usage={"prompt_tokens": 3, "completion_tokens": 0})

    monkeypatch.setitem(PROVIDERS, "probe_failing_usage", _AllFail)
    chain = [
        {"provider": "probe_failing_usage", "model": "primary-model"},
        {"provider": "probe_failing_usage", "model": "backup-model"},
    ]

    resp = await chat_with_fallback(chain, [{"role": "user", "content": "hi"}], max_empty_retries=0)

    assert not resp.ok
    usage_lines = [line for line in fake_logger.lines if "本次请求消耗" in line]
    assert len(usage_lines) == 1
    assert "model=backup-model" in usage_lines[0]


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
    assert sink["prompt_tokens"] == 7 and sink["completion_tokens"] == 3
    # 保留键：实际服务的模型名（调用方据此拼日志/遥测，不是 token 数）
    assert sink[SERVED_MODEL_KEY] == "m"
    assert fake_logger.lines == []

    # 调用方拿着 sink 打一行（多轮再合并就是全程总量）
    log_token_usage(sink, model=sink[SERVED_MODEL_KEY], stream=True)
    assert len(fake_logger.lines) == 1
    assert "流式" in fake_logger.lines[0]
    assert "model=m" in fake_logger.lines[0]


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


class _FailingStreamProvider:
    """主模型：首个事件之前就抛异常（触发流式回退）。"""

    name = "probe_failing_stream"

    def __init__(self, config: dict) -> None:
        self.config = config or {}

    def supports(self, _capability: str) -> bool:
        return True

    async def chat_stream(self, messages, **kwargs):
        raise RuntimeError("boom")
        yield  # pragma: no cover - 让本方法保持异步生成器


async def test_stream_fallback_logs_served_model_not_primary(monkeypatch, fake_logger):
    """流式回退到 backup：没有 sink 时那一行必须写 backup 的模型名。"""
    monkeypatch.setitem(PROVIDERS, "probe_failing_stream", _FailingStreamProvider)
    monkeypatch.setitem(PROVIDERS, "probe_stream_usage", _StreamUsageProvider)
    chain = [
        {"provider": "probe_failing_stream", "model": "primary-model"},
        {"provider": "probe_stream_usage", "model": "backup-model"},
    ]

    events = [
        ev
        async for ev in iter_stream_with_fallback(chain, [{"role": "user", "content": "hi"}])
    ]

    assert [ev.type for ev in events] == ["text", "usage"]
    usage_lines = [line for line in fake_logger.lines if "本次请求消耗" in line]
    assert len(usage_lines) == 1
    assert "model=backup-model" in usage_lines[0]
    assert "primary-model" not in usage_lines[0]


async def test_stream_fallback_records_served_model_into_sink(monkeypatch, fake_logger):
    """带 sink（流式工具循环）：实际模型名写进保留键，供调用方拼那一行。"""
    monkeypatch.setitem(PROVIDERS, "probe_failing_stream", _FailingStreamProvider)
    monkeypatch.setitem(PROVIDERS, "probe_stream_usage", _StreamUsageProvider)
    chain = [
        {"provider": "probe_failing_stream", "model": "primary-model"},
        {"provider": "probe_stream_usage", "model": "backup-model"},
    ]
    sink: dict = {}

    events = [
        ev
        async for ev in iter_stream_with_fallback(
            chain, [{"role": "user", "content": "hi"}], usage_sink=sink
        )
    ]

    assert [ev.type for ev in events] == ["text", "usage"]
    assert sink[SERVED_MODEL_KEY] == "backup-model"
    # 带 sink 时由调用方打那一行：这里只留回退 warning，不打 token 行
    assert not [line for line in fake_logger.lines if "本次请求消耗" in line]


async def test_stream_all_empty_still_names_last_attempted_model(monkeypatch, fake_logger):
    """全部 provider 零产出：没有「实际服务者」，保留键退化为最后一个尝试过的模型。"""

    class _AlwaysEmpty:
        name = "probe_empty_stream"

        def __init__(self, config: dict) -> None:
            self.config = config or {}

        def supports(self, _capability: str) -> bool:
            return True

        async def chat_stream(self, messages, **kwargs):
            return
            yield  # pragma: no cover - 让本方法保持异步生成器

    monkeypatch.setitem(PROVIDERS, "probe_empty_stream", _AlwaysEmpty)
    chain = [
        {"provider": "probe_empty_stream", "model": "primary-model"},
        {"provider": "probe_empty_stream", "model": "backup-model"},
    ]
    sink: dict = {}

    events = [
        ev
        async for ev in iter_stream_with_fallback(
            chain, [{"role": "user", "content": "hi"}], max_empty_retries=0,
            empty_retry_backoff=0, usage_sink=sink,
        )
    ]

    assert events == []
    assert sink[SERVED_MODEL_KEY] == "backup-model"


async def test_stream_sink_keeps_model_when_client_breaks_early(monkeypatch, fake_logger):
    """客户端提前 break（拿到足够句子就停）：保留键仍要留下实际模型名。"""
    monkeypatch.setitem(PROVIDERS, "probe_stream_usage", _StreamUsageProvider)
    sink: dict = {}

    async for _ev in iter_stream_with_fallback(
        [{"provider": "probe_stream_usage", "model": "m"}],
        [{"role": "user", "content": "hi"}],
        usage_sink=sink,
    ):
        break

    assert sink[SERVED_MODEL_KEY] == "m"


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


async def test_chat_with_fallback_aggregates_usage_across_tool_rounds(monkeypatch):
    """非流式工具循环：多轮 usage 求和后再交给调用方（也修了遥测只记最后一轮的老问题）。

    P1 收口后适配器只发单次请求，循环在 ``providers.chat_with_fallback`` 的兼容壳里
    （内部走 ``loop/tool_step.run_blocking_step``）——所以这里测的是那个公开入口。
    """
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
    from app.agent import providers as providers_module

    monkeypatch.setattr(providers_module, "get_provider", lambda cfg: provider)
    resp = await providers_module.chat_with_fallback(
        [{"provider": "openai", "model": "m"}],
        [{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "t"}}],
        tool_executor=_executor,
    )
    assert resp.text == "done"
    assert resp.usage["prompt_tokens"] == 250
    assert resp.usage["completion_tokens"] == 30
    assert resp.usage["prompt_cache_hit_tokens"] == 160
