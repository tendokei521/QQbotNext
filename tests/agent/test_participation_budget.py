"""参与度预算的契约：5 分钟上限按 30 秒粒度折算执行。

回归背景：只有 5 分钟窗口时，开局 30 秒就能把 60 次额度打满，之后 4 分半只能沉默；
「请求节奏」实际由突发决定。现在保留 5 分钟总量不变，另加 30 秒折算闸门：
任意 30 秒最多用掉上限的 1/10（60 次 → 每 30 秒 6 次，10M tokens → 每 30 秒 1M）。
"""

from __future__ import annotations

import time

from app.agent.participation import (
    BUDGET_STEP_SECONDS,
    BUDGET_STEPS_PER_WINDOW,
    BUDGET_WINDOW_SECONDS,
    DEFAULT_MAX_REQUESTS,
    DEFAULT_MAX_TOKENS,
    Budget,
    step_quota,
)


def test_step_window_constants():
    assert BUDGET_WINDOW_SECONDS == 300
    assert BUDGET_STEP_SECONDS == 30
    assert BUDGET_STEPS_PER_WINDOW == 10


def test_step_quota_is_one_tenth_of_5min_limit():
    assert step_quota(DEFAULT_MAX_REQUESTS) == 6
    assert step_quota(DEFAULT_MAX_TOKENS) == 1_000_000
    # 低上限折算不足 1 时仍给 1 次/30 秒机会（5 分钟总量闸门兜底，不会放大预算）
    assert step_quota(5) == 1


def test_burst_is_capped_by_30s_quota():
    budget = Budget()
    for _ in range(6):
        budget.record(0)
    # 6 次正好用满 30 秒额度：即使 5 分钟总账远未到 60，也要拦下
    assert not budget.allow(DEFAULT_MAX_REQUESTS, DEFAULT_MAX_TOKENS)
    usage = budget.usage()
    assert usage["requests"] == 6
    assert usage["step_requests"] == 6


def test_30s_window_rolls_forward():
    budget = Budget()
    now = time.time()
    # 31 秒前的 6 次已滚出 30 秒粒度窗口 → 允许下一次请求
    budget.requests.extend([now - 31] * 6)
    assert budget.allow(DEFAULT_MAX_REQUESTS, DEFAULT_MAX_TOKENS)


def test_5min_total_cap_still_enforced():
    budget = Budget()
    now = time.time()
    # 60 次全部落在 5 分钟内、最近 30 秒为 0：节奏闸门放行，总量闸门必须拦下
    budget.requests.extend([now - 40 - i * 4 for i in range(60)])
    assert not budget.allow(DEFAULT_MAX_REQUESTS, DEFAULT_MAX_TOKENS)
    usage = budget.usage()
    assert usage["requests"] == 60
    assert usage["step_requests"] == 0


def test_token_step_quota_is_enforced():
    budget = Budget()
    now = time.time()
    budget.tokens.append((now, 1_000_000))
    assert not budget.allow(DEFAULT_MAX_REQUESTS, DEFAULT_MAX_TOKENS)
    # 抬高一倍上限后同样的 30 秒用量就合规（说明拦的是折算额度而不是总量）
    assert budget.allow(DEFAULT_MAX_REQUESTS, DEFAULT_MAX_TOKENS * 2)


def test_token_5min_total_cap_still_enforced():
    budget = Budget()
    now = time.time()
    # 10M token 记在 40 秒前：不在 30 秒粒度窗口内，但 5 分钟总量已满
    budget.tokens.append((now - 40, DEFAULT_MAX_TOKENS))
    assert not budget.allow(DEFAULT_MAX_REQUESTS, DEFAULT_MAX_TOKENS)
    usage = budget.usage()
    assert usage["tokens"] == DEFAULT_MAX_TOKENS
    assert usage["step_tokens"] == 0
