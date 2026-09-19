"""空回复重试（empty_reply_retries）回归测试。

背景（远端日志 2026-09-19 18:58）：

    流式 API 请求 -> group_466052056 (消息数: 17)
    流式模型返回空回复，使用兜底文本 (已执行工具数=0)
    ...61s 后：[Api] 回复 27 字符 | 输入 454 / 输出 776 tokens

即请求「成功结束」却没有任何可展示内容（网关提前断流 / 只回思考内容），
用户直接收到兜底话术「抱歉，我暂时无法回答」。本文件锁住：

1. 流式无产出 → 同 provider 重试，第 2 次有内容则正常返回，不触发兜底；
2. 一旦已有文本或工具调用产出，绝不重试（否则会重复发送 / 重复执行工具）；
3. 重试次数可配（0 = 关闭），上限 3；
4. 非流式「成功但空内容」同样重试，且仍空时保留 raw 让调用方兜底继续生效。
"""

from __future__ import annotations

from app.llm.chat import DEFAULT_EMPTY_REPLY_RETRIES, _max_empty_retries
from app.llm.providers import PROVIDERS, chat_with_fallback, iter_stream_with_fallback
from app.llm.providers.base import BaseProvider, LLMResponse, StreamEvent

# monkeypatch.setitem(PROVIDERS, name, cls) 后 get_provider(cfg) 会 cls(cfg) 实例化，
# 因此桩类必须在类级别记录调用次数（每个用例各注册一个唯一的 provider 名）。
_CALLS: dict[str, int] = {}
_MODES: dict[str, str] = {}


class _StubProvider(BaseProvider):
    """按类级 MODES 决定行为，并记录调用次数。

    - empty_once：第 1 次零产出，之后产出文本（锁「重试可救回」）
    - always_empty：每次都零产出（锁「转下一个 provider / 保留 raw」）
    - text_once：每次都产出文本（锁「有产出不重试」）
    - tool_only：只产出工具调用（锁「工具调用算有产出」）
    """

    async def chat(self, messages, **kwargs):
        name = self.config.get("provider")
        _CALLS[name] = _CALLS.get(name, 0) + 1
        mode = _MODES[name]
        if mode == "tool_only":
            return LLMResponse(text="", raw={"choices": [{}]}, tool_results=[{"name": "t", "result": "ok"}])
        if mode == "always_empty" or (mode == "empty_once" and _CALLS[name] == 1):
            return LLMResponse(text="", raw={"choices": [{"finish_reason": "stop"}]})
        return LLMResponse(text="重试后的答案", raw={"choices": [{}]})

    async def chat_stream(self, messages, **kwargs):
        name = self.config.get("provider")
        _CALLS[name] = _CALLS.get(name, 0) + 1
        first = _CALLS[name] == 1
        mode = _MODES[name]
        if mode == "tool_only":
            yield StreamEvent(type="tool_call", tool_call={"index": 0, "function": {"name": "t", "arguments": "{}"}})
            yield StreamEvent(type="done", finish_reason="tool_calls")
            return
        if mode == "always_empty" or (mode == "empty_once" and first):
            return  # 零产出：连 done 都不发（模拟网关提前断流）
        yield StreamEvent(type="text", text="第一次就够了" if mode == "text_once" else "答到了")


def _register(monkeypatch, name: str, mode: str, cfg: dict) -> None:
    _CALLS[name] = 0
    _MODES[name] = mode

    class _Bound(_StubProvider):
        pass

    monkeypatch.setitem(PROVIDERS, name, _Bound)
    cfg["provider"] = name


async def _drain(chain, **kwargs):
    events = []
    async for ev in iter_stream_with_fallback(chain, [{"role": "user", "content": "hi"}], **kwargs):
        events.append(ev)
    return events


def _text(events) -> str:
    return "".join(ev.text for ev in events if ev.type == "text")


# ---------- 配置解析 ----------


def test_empty_reply_retries_config_clamped():
    assert _max_empty_retries({}) == DEFAULT_EMPTY_REPLY_RETRIES
    assert _max_empty_retries({"empty_reply_retries": 0}) == 0     # 显式关闭
    assert _max_empty_retries({"empty_reply_retries": 3}) == 3
    assert _max_empty_retries({"empty_reply_retries": 99}) == 3    # 上限保护
    assert _max_empty_retries({"empty_reply_retries": -5}) == 0
    assert _max_empty_retries({"empty_reply_retries": "abc"}) == DEFAULT_EMPTY_REPLY_RETRIES


# ---------- 流式重试 ----------


async def test_stream_empty_then_text_retries_same_provider(monkeypatch):
    """核心回归：第一次零产出，重试后拿到文本，用户不再看到兜底。"""
    cfg: dict = {}
    _register(monkeypatch, "empty_probe", "empty_once", cfg)

    events = await _drain([cfg], empty_retry_backoff=0)

    assert _CALLS["empty_probe"] == 2, "空回复必须触发一次同 provider 重试"
    assert _text(events) == "答到了"


async def test_stream_retries_can_be_disabled(monkeypatch):
    cfg: dict = {}
    _register(monkeypatch, "empty_off_probe", "always_empty", cfg)

    events = await _drain([cfg], max_empty_retries=0, empty_retry_backoff=0)

    assert _CALLS["empty_off_probe"] == 1
    assert _text(events) == ""  # 交给调用方兜底


async def test_stream_never_retries_after_text(monkeypatch):
    """已有文本产出 → 绝不重试（重试会重复发送）。"""
    cfg: dict = {}
    _register(monkeypatch, "once_probe", "text_once", cfg)

    events = await _drain([cfg], empty_retry_backoff=0)

    assert _CALLS["once_probe"] == 1
    assert _text(events) == "第一次就够了"


async def test_stream_tool_call_counts_as_content(monkeypatch):
    """工具调用也是产出：重试会导致工具重复执行，必须禁止。"""
    cfg: dict = {}
    _register(monkeypatch, "tool_probe", "tool_only", cfg)

    events = await _drain([cfg], empty_retry_backoff=0)

    assert _CALLS["tool_probe"] == 1
    assert [ev.type for ev in events] == ["tool_call", "done"]


async def test_stream_falls_over_to_next_provider_when_all_empty(monkeypatch):
    """主 provider 用尽重试仍空 → 换下一个 provider（保留回退链语义）。"""
    primary: dict = {}
    backup: dict = {}
    _register(monkeypatch, "empty_primary", "always_empty", primary)
    _register(monkeypatch, "empty_backup", "ok", backup)

    events = await _drain([primary, backup], max_empty_retries=1, empty_retry_backoff=0)

    assert _CALLS["empty_primary"] == 2   # 1 次原始 + 1 次重试
    assert _CALLS["empty_backup"] == 1
    assert _text(events) == "答到了"


# ---------- 非流式重试 ----------


async def test_chat_empty_then_text_retries_same_provider(monkeypatch):
    cfg: dict = {}
    _register(monkeypatch, "chat_empty_probe", "empty_once", cfg)

    resp = await chat_with_fallback([cfg], [{"role": "user", "content": "hi"}], empty_retry_backoff=0)

    assert _CALLS["chat_empty_probe"] == 2
    assert resp.text == "重试后的答案"


async def test_chat_keeps_raw_when_all_retries_empty(monkeypatch):
    """全部软失败：返回最后一个响应（raw 保留），调用方兜底分支不受影响。"""
    cfg: dict = {}
    _register(monkeypatch, "chat_still_empty", "always_empty", cfg)

    resp = await chat_with_fallback(
        [cfg], [{"role": "user", "content": "hi"}], max_empty_retries=1, empty_retry_backoff=0
    )

    assert _CALLS["chat_still_empty"] == 2
    assert resp.text == ""
    # 所有重试仍空：必须保留 raw，调用方才按「空回复兜底」而不是「请求最终失败」处理
    assert resp.raw is not None
    assert not resp.ok


async def test_chat_tool_results_are_not_treated_as_empty(monkeypatch):
    """只有工具结果、无文本的响应不是空回复，不应重试。"""
    cfg: dict = {}
    _register(monkeypatch, "chat_tool_only", "tool_only", cfg)

    resp = await chat_with_fallback([cfg], [{"role": "user", "content": "hi"}], empty_retry_backoff=0)

    assert _CALLS["chat_tool_only"] == 1
    assert resp.tool_results
