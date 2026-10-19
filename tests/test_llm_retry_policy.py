"""回退层重试决策的回归测试（重试只能有一个归属层）。

背景（线上 2026-10 复盘）：

1. **三层重试相乘**：HTTP 适配器 ``retry_attempts=3`` × 回退层空回复重试 1 ×
   收敛重试 1 × 回退模型数 —— 一轮最多十几次请求，每次重发全量上下文；
2. **确定性失败还在重试**：模型名写错 / key 无效 / 余额不足时，原样重发必然同样失败，
   只是把上下文再烧一遍；
3. **没有总闸门**：谁都不知道"这一轮已经发了 5 次请求、烧了 16 万输入 token"。

本文件锁住修复后的口径：

- 错误冒到回退层 = 适配器内部瞬态重试已用尽 → **只换 provider，不原样重试**；
- 真正该由回退层重试的只有"没报错但零产出"，且额度受 ``TurnBudget`` 约束；
- 确定性失败**停整条链**（换模型也不会成功的那种）；
- ``Retry-After`` 优先于指数退避；
- 工具轮是合法请求，不吃重试额度（``base_requests``）。
"""

from __future__ import annotations

import asyncio

from app.llm.providers import PROVIDERS, chat_with_fallback, iter_stream_with_fallback
from app.llm.providers.base import BaseProvider, LLMResponse, StreamEvent
from app.llm.request_policy import TurnBudget, classify_error_text, classify_response

CALLS: dict[str, int] = {}
BEHAVIOR: dict[str, dict] = {}


class _Stub(BaseProvider):
    """按 BEHAVIOR[name] 决定行为：error 文本 / 零产出 / 正常文本 / 异常。"""

    async def chat(self, messages, **kwargs):
        name = self.config.get("provider")
        CALLS[name] = CALLS.get(name, 0) + 1
        spec = BEHAVIOR[name]
        if spec.get("raise"):
            raise spec["raise"]
        if spec.get("error"):
            return LLMResponse(text="", raw=None)
        if spec.get("empty"):
            return LLMResponse(text="", raw={"finish_reason": spec.get("finish_reason", "stop")})
        return LLMResponse(text=spec.get("text", "好的"), raw={"choices": [{}]})

    async def chat_stream(self, messages, **kwargs):
        name = self.config.get("provider")
        CALLS[name] = CALLS.get(name, 0) + 1
        spec = BEHAVIOR[name]
        if spec.get("raise"):
            raise spec["raise"]
        if spec.get("error"):
            yield StreamEvent(type="error", text=spec["error"])
            return
        if spec.get("empty"):
            yield StreamEvent(type="done", finish_reason=spec.get("finish_reason", "stop"))
            return
        yield StreamEvent(type="text", text=spec.get("text", "好的"))


def _register(monkeypatch, name: str, **spec) -> str:
    key = f"rp_{name}_{len(BEHAVIOR)}"
    CALLS[key] = 0
    BEHAVIOR[key] = spec

    class _Bound(_Stub):
        pass

    monkeypatch.setitem(PROVIDERS, key, _Bound)
    return key


def _cfg(key: str) -> dict:
    return {"provider": key, "model": key, "max_tokens": 1024}


async def _drain(chain, **kwargs):
    return [ev async for ev in iter_stream_with_fallback(chain, [{"role": "user", "content": "hi"}], **kwargs)]


# ---------- 错误冒到回退层：不原样重试 ----------


async def test_transient_error_is_not_retried_at_fallback_layer(monkeypatch):
    """429 冒到回退层说明适配器内部的重试已用尽：这里只换 provider，不原样重发。"""
    bad = _register(monkeypatch, "bad", error="HTTP 429: rate limited")
    good = _register(monkeypatch, "good", text="备用模型的回答")

    events = await _drain([_cfg(bad), _cfg(good)], max_empty_retries=2, empty_retry_backoff=0)

    assert CALLS[bad] == 1, "回退层不该重复请求同一个 provider"
    assert CALLS[good] == 1
    assert "".join(ev.text for ev in events if ev.type == "text") == "备用模型的回答"


async def test_deterministic_error_stops_the_whole_chain(monkeypatch):
    """模型不存在 / key 无效 / 余额不足：换模型或原样重发都不会成功 —— 停，别烧上下文。"""
    bad = _register(monkeypatch, "deterministic", error="错误: 模型不存在 (model_not_found)")
    good = _register(monkeypatch, "unused", text="不该被请求")

    events = await _drain([_cfg(bad), _cfg(good)], max_empty_retries=2, empty_retry_backoff=0)

    assert CALLS[bad] == 1
    assert CALLS[good] == 0, "确定性失败后不该再试下一个 provider"
    assert events and events[-1].type == "error"


async def test_empty_output_is_retried_within_allowance(monkeypatch):
    """没报错但零产出（网关提前断流）：这才是回退层该重试的情况。"""
    key = _register(monkeypatch, "empty", empty=True)

    await _drain([_cfg(key)], max_empty_retries=2, empty_retry_backoff=0)

    assert CALLS[key] == 3, "1 次原始 + 2 次重试"


async def test_truncated_output_is_not_retried(monkeypatch):
    """被 max_tokens 截断：要的是更大预算，原样重发只会把同样预算再烧一遍。"""
    key = _register(monkeypatch, "cut", empty=True, finish_reason="length")

    await _drain([_cfg(key)], max_empty_retries=2, empty_retry_backoff=0)

    assert CALLS[key] == 1


# ---------- 总闸门 ----------


async def test_budget_caps_the_number_of_requests(monkeypatch):
    """档位给的是**总账**：额度用完后连零产出的重试也不许再发。"""
    key = _register(monkeypatch, "capped", empty=True)
    budget = TurnBudget(profile="thrifty")  # 2 次重试额度、同模型最多重试 1 次

    await _drain([_cfg(key)], max_empty_retries=3, empty_retry_backoff=0, budget=budget)

    assert CALLS[key] == 2, "thrifty 只允许 2 次请求"
    assert budget.summary()["requests"] == 2
    # 闸门自己的拦截面：用尽时必须给出原因（不许静默按"空回复"处理）
    allowed, why = budget.allow()
    assert not allowed
    assert "用尽" in why and "2/2" in why


async def test_tool_rounds_do_not_consume_retry_allowance(monkeypatch):
    """工具轮是用户配置的合法多次请求，不能被重试额度掐死。"""
    budget = TurnBudget.from_config({"retry_profile": "balanced", "max_tool_rounds": 5})

    assert budget.base_requests == 5
    assert budget.retry_requests == 4
    assert budget.max_requests == 9
    for i in range(9):
        allowed, why = budget.allow()
        assert allowed, f"第 {i + 1} 次被拦：{why}"
        budget.charge(estimated_input=100)
    assert not budget.allow()[0]


async def test_budget_charges_real_usage_when_available(monkeypatch):
    """上游报了真实 prompt_tokens 就用真实值记账（估算可能差两三倍）。"""
    budget = TurnBudget(profile="balanced")
    budget.charge(estimated_input=999, usage={"prompt_tokens": 1234})
    budget.charge(estimated_input=50)

    assert budget.input_tokens == 1284
    assert budget.requests == 2


# ---------- Retry-After ----------


async def test_retry_after_header_is_honoured(monkeypatch):
    """上游让等多久就等多久，别用 1s/2s/4s 去撞同一道限流墙。"""
    from app.llm.providers import openai_compat

    sleeps: list[float] = []

    async def _fake_sleep(seconds):
        sleeps.append(seconds)

    attempts = {"n": 0}
    real_error = openai_compat._RetryableError("HTTP 429: slow down", code="HTTP 429", retry_after=7.5)

    async def _fake_post(self, api_key, payload, timeout):
        attempts["n"] += 1
        raise real_error

    async def _fake_stream_once(self, api_key, payload, timeout):
        attempts["n"] += 1
        raise real_error
        yield  # pragma: no cover —— 让它保持 async generator 形态

    monkeypatch.setattr(openai_compat.asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(openai_compat.OpenAICompatProvider, "_post", _fake_post)
    monkeypatch.setattr(openai_compat.OpenAICompatProvider, "_stream_once", _fake_stream_once)

    provider = openai_compat.OpenAICompatProvider(
        {"api_key": "sk-x", "model": "m", "retry_attempts": 2}
    )

    assert await provider._request({"model": "m"}, 10) is None
    assert attempts["n"] == 2, "retry_attempts=2 表示最多 2 次请求"
    assert sleeps == [7.5], f"应按 Retry-After 退避，实际 {sleeps}"

    sleeps.clear()
    attempts["n"] = 0
    events = [ev async for ev in provider.chat_stream([{"role": "user", "content": "hi"}])]

    assert attempts["n"] == 2
    assert sleeps == [7.5]
    assert events and events[-1].type == "error"


async def test_retry_attempts_defaults_to_two():
    """默认 2 次（不是 3）：每一层都重发全量上下文，省一次就是省一倍输入 token。"""
    from app.llm.providers import openai_compat

    assert openai_compat.OpenAICompatProvider({"api_key": "k"}).max_retries == 2
    assert openai_compat.OpenAICompatProvider({"api_key": "k", "retry_attempts": 5}).max_retries == 5


async def test_backoff_falls_back_to_exponential_without_header(monkeypatch):
    """没有 Retry-After 才用指数退避。"""
    from app.llm.providers import openai_compat

    sleeps: list[float] = []

    async def _fake_sleep(seconds):
        sleeps.append(seconds)

    async def _boom(self, api_key, payload, timeout):
        raise openai_compat._RetryableError("HTTP 503: down", code="HTTP 503")

    monkeypatch.setattr(openai_compat.asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(openai_compat.OpenAICompatProvider, "_post", _boom)

    provider = openai_compat.OpenAICompatProvider({"api_key": "k", "retry_attempts": 3})
    assert await provider._request({"model": "m"}, 10) is None

    assert sleeps == [1, 2], f"应指数退避 1s→2s，实际 {sleeps}"


# ---------- 分类口径 ----------


def test_error_text_classification_matches_exception_classification():
    """错误事件（只有文案）与异常走同一套口径，不能一个判瞬态一个判确定性。"""
    assert classify_error_text("HTTP 429: rate limited").kind.value == "transient"
    assert classify_error_text("HTTP 500: oops").kind.value == "transient"
    assert classify_error_text("模型不存在").kind.value == "deterministic"
    assert classify_error_text("余额不足").kind.value == "deterministic"
    assert classify_error_text("上下文超长").kind.value == "deterministic"
    assert classify_error_text("something weird").kind.value == "transient"


def test_classify_response_distinguishes_the_three_empty_shapes():
    """三种"空"要分开：被截断 / 只有思考 / 计费了却没产出。"""
    cut = classify_response(text="", finish_reason="length", usage={"completion_tokens": 8192})
    think = classify_response(text="", reasoning="想了很久", usage={"completion_tokens": 8192})
    suspect = classify_response(text="", usage={"prompt_tokens": 2000, "completion_tokens": 0})

    assert cut.kind.value == "empty_cut"
    assert think.kind.value == "empty_think"
    assert suspect.kind.value == "empty_suspect"
    assert think.retry_same is False, "只有思考：换问法才可能有用，原样重发必然同样打满"


# ---------- 非流式同口径 ----------


async def test_chat_fallback_stops_on_deterministic_error(monkeypatch):
    """非流式路径同样在确定性失败处停下（401 换 key 也没用，别再去撞下一个模型）。"""
    bad = _register(monkeypatch, "chat_det", **{"raise": ValueError("HTTP 401: invalid api key")})
    good = _register(monkeypatch, "chat_unused", text="不该被请求")

    resp = await chat_with_fallback([_cfg(bad), _cfg(good)], [{"role": "user", "content": "hi"}])

    assert CALLS[bad] == 1
    assert CALLS[good] == 0
    assert resp.text == ""


async def test_chat_fallback_reports_budget_exhaustion(monkeypatch):
    """非流式同样受总闸门约束，且把"用尽"写清楚。"""
    key = _register(monkeypatch, "chat_empty", empty=True)
    budget = TurnBudget(profile="thrifty")

    resp = await chat_with_fallback(
        [_cfg(key)], [{"role": "user", "content": "hi"}], max_empty_retries=3, budget=budget
    )

    assert CALLS[key] == 2
    assert resp.text == ""
    assert resp.raw is not None, "软失败要保留 raw，调用方才好按「空回复兜底」处理"


async def test_no_budget_keeps_legacy_behaviour(monkeypatch):
    """没有 budget（直调/插件调用）时沿用 ``max_empty_retries``，行为不倒退。"""
    key = _register(monkeypatch, "legacy", empty=True)

    await _drain([_cfg(key)], max_empty_retries=1, empty_retry_backoff=0)

    assert CALLS[key] == 2


def test_asyncio_sleep_is_the_only_timer_used():
    """退避一律走 asyncio.sleep（可被 monkeypatch 替换），不允许 time.sleep 阻塞事件循环。"""
    import inspect

    from app.llm.providers import openai_compat

    source = inspect.getsource(openai_compat)
    assert "time.sleep" not in source
    assert asyncio is not None
