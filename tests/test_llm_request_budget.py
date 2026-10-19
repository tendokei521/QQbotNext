"""请求合理性（P3）：预算收口 + 窗口感知 + 装不下时的可操作提示。

背景：此前只有 ``context_token_budget`` 一个软阈值（默认 0 = 关闭），而且它
**看不见模型窗口**，也看不见 ``max_tokens``。于是三种"装不下"全都要等上游 400 才知道：

1. 历史太长（该砍历史）；
2. ``max_tokens`` 太大（该调小输出预算，砍历史没用）；
3. 必留内容本身就超（怎么裁都没用）。

本文件锁住 ``token_budget.request_limit`` / ``apply_request_policy`` 的口径：
输入上限 = 窗口 × (1−余量) − max_tokens，并且软阈值只能是它的下界。
"""

from __future__ import annotations

from app.llm.token_budget import (
    MIN_INPUT_FLOOR,
    WINDOW_SAFETY_RATIO,
    apply_request_policy,
    estimate_tokens,
    request_limit,
)


def _msgs(rounds: int, *, chars: int = 200) -> list[dict]:
    out = [{"role": "system", "content": "你是助手"}]
    for i in range(rounds):
        out.append({"role": "user", "content": f"用户第 {i} 轮：" + "字" * chars})
        out.append({"role": "assistant", "content": "回" * chars})
    out.append({"role": "user", "content": "本轮提问"})
    return out


# ---------- request_limit ----------


def test_window_reserves_output_budget():
    """输入上限必须先扣掉 max_tokens：输入 + 输出才是窗口占用。"""
    limit, why = request_limit({}, max_tokens=4096, context_window=32768)
    expected = int(32768 * (1 - WINDOW_SAFETY_RATIO)) - 4096

    assert limit == expected
    assert "32768" in why and "4096" in why


def test_bigger_output_budget_shrinks_input_allowance():
    """max_tokens 越大，能放下的历史越少 —— 这正是"调大输出"要付的代价。"""
    small, _ = request_limit({}, max_tokens=1024, context_window=32768)
    large, _ = request_limit({}, max_tokens=16384, context_window=32768)

    assert large < small


def test_soft_budget_caps_the_window_limit():
    """用户自定的软阈值只能是窗口上限的**下界**（取更严的那个）。"""
    limit, why = request_limit(
        {"context_token_budget": 2000}, max_tokens=1024, context_window=32768
    )

    assert limit == 2000
    assert "2000" in why


def test_soft_budget_applies_when_window_unknown():
    """不知道窗口时退化为旧行为（context_token_budget），不倒退。"""
    limit, why = request_limit({"context_token_budget": 3000})

    assert limit == 3000
    assert "context_token_budget" in why


def test_no_limit_when_nothing_configured():
    """什么都没配 = 不限（保持默认零打扰），但会打 debug 提示可以填窗口。"""
    assert request_limit({}) == (0, "")


def test_limit_never_goes_below_floor():
    """max_tokens 比窗口还大时不返回负数：留给"必留内容"一个下限。"""
    limit, _ = request_limit({}, max_tokens=999999, context_window=8192)

    assert limit == MIN_INPUT_FLOOR


# ---------- apply_request_policy ----------


def test_oversized_history_is_trimmed_from_the_oldest():
    messages = _msgs(40, chars=400)
    before = estimate_tokens(messages)

    out = apply_request_policy(
        messages, {}, max_tokens=1024, context_window=8192, bot_id=1
    )

    assert estimate_tokens(out) <= request_limit({}, max_tokens=1024, context_window=8192)[0]
    assert len(out) < len(messages)
    # 必留：system 与本轮提问
    assert out[0]["role"] == "system"
    assert out[-1]["content"] == "本轮提问"
    assert before > estimate_tokens(out)


def test_within_limit_is_untouched():
    messages = _msgs(1)

    out = apply_request_policy(messages, {}, max_tokens=1024, context_window=32768)

    assert out == messages


def test_input_is_not_mutated():
    """收口函数要返回新列表：调用方的 messages 后面还要用于重组/落盘。"""
    messages = _msgs(40, chars=400)
    snapshot = list(messages)

    out = apply_request_policy(messages, {}, max_tokens=1024, context_window=8192)

    assert out is not messages
    assert messages == snapshot


def test_unfixable_case_still_returns_something():
    """必留内容本身就超：不裁到空、不抛异常，返回原样并交给上游报错。"""
    messages = [
        {"role": "system", "content": "人设" * 5000},
        {"role": "user", "content": "本轮提问"},
    ]

    out = apply_request_policy(messages, {}, max_tokens=1024, context_window=8192)

    assert len(out) == 2
    assert out[-1]["content"] == "本轮提问"


def test_no_window_no_budget_keeps_everything():
    messages = _msgs(30)

    out = apply_request_policy(messages, {})

    assert out == messages
