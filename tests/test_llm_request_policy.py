"""provider 层唯一决策点的验收：配置合并溯源、max_tokens 归一化、失败分类、请求预算。"""

from __future__ import annotations

import asyncio
import types

import pytest

from app.agent.providers.policy import (
    DEFAULT_RETRY_PROFILE,
    LAYER_AGENT,
    LAYER_DEFAULT,
    LAYER_MODEL,
    LAYER_PRESET,
    REASONING_FLOOR,
    FailureKind,
    TurnBudget,
    classify_exception,
    classify_response,
    is_reasoning_model,
    merge_config_layers,
    plan_max_tokens,
    retry_profile_of,
)

DEFAULTS = {"max_tokens": 1024, "temperature": 0.7, "timeout": 60, "retry_attempts": 3}


# ── 1. 合并与溯源 ──────────────────────────────────────────────────────


def test_model_layer_wins_and_is_reported_as_origin():
    eff = merge_config_layers(
        base={"max_tokens": 1024},
        agent_layer={"max_tokens": 16384},
        preset_layer={},
        model_layer={"max_tokens": 8192},
        defaults=DEFAULTS,
    )
    assert eff.max_tokens == 8192
    assert eff.max_tokens_origin == LAYER_MODEL


def test_preset_layer_wins_over_agent():
    eff = merge_config_layers(
        base={"max_tokens": 1024},
        agent_layer={"max_tokens": 16384},
        preset_layer={"max_tokens": 4096},
        model_layer={},
        defaults=DEFAULTS,
    )
    assert eff.max_tokens == 4096
    assert eff.max_tokens_origin == LAYER_PRESET


def test_agent_explicit_is_reported_when_provider_layers_are_silent():
    """关键：链路没设时才该报 agent —— 而不是因为 base 铺满了默认值就永远报 agent。"""
    eff = merge_config_layers(
        base={"max_tokens": 16384},  # 铺满默认值的那一份，值是用户设的
        agent_layer={"max_tokens": 16384},  # 显式层
        preset_layer={},
        model_layer={},
        defaults=DEFAULTS,
    )
    assert eff.max_tokens == 16384
    assert eff.max_tokens_origin == LAYER_AGENT


def test_default_origin_when_nobody_set_it():
    """用户没动过、链路也没设 → 必须如实说"内置默认"，不能谎称来自 agent。"""
    eff = merge_config_layers(
        base={"max_tokens": 1024},
        agent_layer={},
        preset_layer={},
        model_layer={},
        defaults=DEFAULTS,
    )
    assert eff.max_tokens_origin == LAYER_DEFAULT


def test_empty_string_counts_as_not_set():
    """``max_tokens=""`` 是"清除覆盖"的写法，不能把空串当成一个值。"""
    eff = merge_config_layers(
        base={"max_tokens": 1024},
        agent_layer={"max_tokens": 16384},
        preset_layer={"max_tokens": ""},
        model_layer={"max_tokens": None},
        defaults=DEFAULTS,
    )
    assert eff.max_tokens == 1024
    assert eff.max_tokens_origin == LAYER_AGENT


def test_merge_keeps_unrelated_agent_keys():
    """合并结果必须仍然带着 agent 的其它键（provider 层还会从链上读它们）。"""
    eff = merge_config_layers(
        base={"max_tokens": 1024, "image_max_count": 6, "group_enable": True},
        preset_layer={"api_key": "sk-x"},
        model_layer={"model": "gpt-4o"},
        defaults=DEFAULTS,
    )
    assert eff.config["image_max_count"] == 6
    assert eff.config["group_enable"] is True
    assert eff.config["api_key"] == "sk-x"
    assert eff.config["model"] == "gpt-4o"
    assert eff.config["_max_tokens_origin"] == LAYER_DEFAULT  # 兼容既有读取口径


# ── 2. max_tokens 归一化 ───────────────────────────────────────────────


def test_plain_model_keeps_its_budget():
    plan = plan_max_tokens({"max_tokens": 1024, "model": "gpt-4o"})
    assert plan.value == 1024
    assert not plan.notes


def test_reasoning_model_budget_is_raised_to_floor():
    """思考模型设 1024 = 让正文必然为空，必须抬到下限并留痕。"""
    plan = plan_max_tokens({"max_tokens": 1024, "model": "deepseek-reasoner"})
    assert plan.value == REASONING_FLOOR
    assert any("思考模型下限" in n for n in plan.notes)


def test_reasoning_flag_beats_model_name():
    assert is_reasoning_model({"model": "gpt-4o", "reasoning": True}) is True
    assert is_reasoning_model({"model": "deepseek-reasoner", "reasoning": False}) is False


def test_budget_is_capped_by_declared_window():
    plan = plan_max_tokens({"max_tokens": 999999, "context_window": 8192})
    assert plan.value == 8192
    assert any("窗口" in n for n in plan.notes)


def test_inherit_uses_window_quarter_when_unset():
    plan = plan_max_tokens({"context_window": 32000})
    assert plan.value == 8000


def test_origin_is_carried_into_the_plan():
    plan = plan_max_tokens({"max_tokens": 2048, "_max_tokens_origin": LAYER_MODEL})
    assert plan.origin == LAYER_MODEL
    assert "provider 模型" in plan.describe()


# ── 3. 失败分类 ────────────────────────────────────────────────────────


class _Http(Exception):
    def __init__(self, status, message="boom", headers=None):
        super().__init__(message)
        self.status_code = status
        self.response = types.SimpleNamespace(status_code=status, headers=headers or {})


@pytest.mark.parametrize("status", [408, 409, 429, 500, 502, 503, 504, 529])
def test_retryable_status_is_transient(status):
    failure = classify_exception(_Http(status))
    assert failure.kind == FailureKind.TRANSIENT
    assert failure.retry_same is True


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_client_errors_are_deterministic_and_stop_the_chain(status):
    failure = classify_exception(_Http(status))
    assert failure.kind == FailureKind.DETERMINISTIC
    assert failure.retry_same is False
    assert failure.switch_provider is False, "参数/权限错换模型也没用，不该白试一遍"


def test_rate_limit_retry_after_is_honoured():
    failure = classify_exception(_Http(429, headers={"retry-after": "7"}))
    assert failure.retry_after == 7.0


def test_context_length_error_is_deterministic_even_without_status():
    failure = classify_exception(ValueError("This model's maximum context length is 8192 tokens"))
    assert failure.kind == FailureKind.DETERMINISTIC


def test_timeout_is_transient():
    assert classify_exception(asyncio.TimeoutError()).kind == FailureKind.TRANSIENT
    assert classify_exception(ConnectionResetError()).kind == FailureKind.TRANSIENT


def test_unknown_exception_falls_back_to_transient_once():
    """未知异常里混着最多的是网关抖动；一律当确定性会让"抖一下就整轮失败"。"""
    failure = classify_exception(RuntimeError("weird"))
    assert failure.kind == FailureKind.TRANSIENT


def test_real_output_is_not_a_failure():
    assert classify_response(text="你好") is None
    assert classify_response(text="", tool_results=[{"ok": True}]) is None


def test_cut_by_length_suggests_raising_the_budget():
    failure = classify_response(finish_reason="length", max_tokens=8192, usage={"completion_tokens": 8192})
    assert failure.kind == FailureKind.EMPTY_CUT
    assert failure.retry_same is True, "抬预算再要一次是有意义的"


def test_thinking_only_never_retries_the_same_prompt():
    """同样的输入必然同样打满预算 —— 原样重发就是烧钱（线上为此烧了 16.7 万 token）。"""
    failure = classify_response(reasoning="我想了很久……", finish_reason="stop")
    assert failure.kind == FailureKind.EMPTY_THINK
    assert failure.retry_same is False
    assert failure.switch_provider is False


def test_billed_but_empty_is_suspect():
    failure = classify_response(usage={"prompt_tokens": 167048, "completion_tokens": 8192})
    assert failure.kind == FailureKind.EMPTY_SUSPECT


def test_unbilled_empty_is_plain_transient():
    failure = classify_response()
    assert failure.kind == FailureKind.TRANSIENT


# ── 4. 请求预算 ────────────────────────────────────────────────────────


def test_profile_defaults_to_balanced():
    assert retry_profile_of({}) == DEFAULT_RETRY_PROFILE
    assert retry_profile_of({"retry_profile": "thrifty"}) == "thrifty"
    assert retry_profile_of({"retry_profile": "nonsense"}) == DEFAULT_RETRY_PROFILE


def test_request_cap_is_enforced():
    budget = TurnBudget(profile="thrifty")
    assert budget.max_requests == 2
    assert budget.allow()[0] is True
    budget.charge()
    assert budget.allow()[0] is True
    budget.charge()
    allowed, why = budget.allow(reason="换下一个 provider")
    assert allowed is False
    assert "已用尽" in why and "换下一个 provider" in why


def test_input_token_budget_stops_a_rerun_of_the_same_huge_context():
    """线上事故形状：16.7 万 token 的上下文被原样重发三次。"""
    budget = TurnBudget(max_input_tokens=200000)
    assert budget.allow(estimated_input=167048)[0] is True
    budget.charge(estimated_input=167048)
    allowed, why = budget.allow(estimated_input=167048, reason="空回复重试")
    assert allowed is False
    assert "将超预算" in why


def test_real_usage_beats_estimate():
    budget = TurnBudget()
    budget.charge(estimated_input=100, usage={"prompt_tokens": 5000})
    assert budget.input_tokens == 5000


def test_budget_summary_reports_what_was_denied():
    budget = TurnBudget(profile="thrifty")
    budget.charge()
    budget.charge()
    budget.allow(reason="重试")
    summary = budget.summary()
    assert summary["requests"] == 2
    assert summary["max_requests"] == 2
    assert summary["denied"]


def test_budget_from_config_reads_turn_limit():
    budget = TurnBudget.from_config({"retry_profile": "resilient", "turn_token_budget": 90000})
    assert budget.profile == "resilient"
    assert budget.max_input_tokens == 90000
