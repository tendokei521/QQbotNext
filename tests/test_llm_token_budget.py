"""上下文 token 预算（对齐 AstrBot ContextManager 思路）。

框架原本只有按条数的历史压缩（``history_rounds`` + LLM 摘要），没有「这一轮总共
允许多大」的上限。这里锁住可选的预算闸门（``context_token_budget > 0`` 才生效）：
超预算从最旧历史丢起，system（人设/协议）与最后一条 user（本轮提问）永不丢。
"""

from __future__ import annotations

from app.llm import token_budget as tb
from app.llm.assembly import apply_post_assemble
from app.llm.hooks import LlmHookRegistry


class _Config:
    def __init__(self, data=None):
        self.data = dict(data or {})

    def get(self, key, default=None):
        return self.data.get(key, default)


class _Runtime:
    bot_id = 555

    def __init__(self, config=None):
        self.config = _Config(config or {})
        self.llm_hooks = LlmHookRegistry()


def _long_history(rounds: int = 10, chars: int = 400) -> list[dict]:
    """system + 多轮历史 + 本轮提问。"""
    messages = [{"role": "system", "content": "人设" * 10}]
    for i in range(rounds):
        messages.append({"role": "user", "content": f"历史{i}" + "x" * chars})
        messages.append({"role": "assistant", "content": f"回复{i}" + "y" * chars})
    messages.append({"role": "user", "content": "本轮提问"})
    return messages


# ==================== 估算 ====================


def test_estimate_tokens_counts_text_and_overhead():
    assert tb.estimate_tokens([]) == 0
    one = tb.estimate_tokens([{"role": "user", "content": "abcd"}], per_token_chars=1)
    assert one == tb.PER_MESSAGE_OVERHEAD + 4


def test_estimate_tokens_handles_multimodal_and_tool_calls():
    multimodal = [{"role": "user", "content": [{"type": "text", "text": "ab"}, {"type": "image_url", "image_url": {"url": "u"}}]}]
    assert tb.estimate_tokens(multimodal, per_token_chars=1) > tb.PER_MESSAGE_OVERHEAD + 2
    with_tools = [{"role": "assistant", "content": "", "tool_calls": [{"id": "1", "function": {"name": "f"}}]}]
    assert tb.estimate_tokens(with_tools, per_token_chars=1) > tb.PER_MESSAGE_OVERHEAD


def test_estimate_tolerates_garbage():
    assert tb.estimate_tokens(None) == 0
    assert tb.estimate_tokens([None, 1, "x"]) == 0  # 非 dict 条目直接跳过


# ==================== 配置 ====================


def test_budget_disabled_by_default():
    assert tb.budget(_Config()) == 0
    assert tb.budget(_Config({"context_token_budget": "abc"})) == 0
    assert tb.budget(_Config({"context_token_budget": 1000})) == 1000


def test_chars_per_token_falls_back():
    assert tb.chars_per_token(_Config()) == tb.DEFAULT_CHARS_PER_TOKEN
    assert tb.chars_per_token(_Config({"context_token_chars_per_token": 0})) == tb.DEFAULT_CHARS_PER_TOKEN
    assert tb.chars_per_token(_Config({"context_token_chars_per_token": "x"})) == tb.DEFAULT_CHARS_PER_TOKEN
    assert tb.chars_per_token(_Config({"context_token_chars_per_token": 4})) == 4


# ==================== 裁剪 ====================


def test_under_budget_is_untouched():
    messages = _long_history(rounds=1, chars=10)
    assert tb.apply_budget(messages, _Config({"context_token_budget": 100000})) == messages


def test_disabled_budget_is_untouched():
    messages = _long_history(rounds=3, chars=400)
    assert tb.apply_budget(messages, _Config()) == messages
    assert tb.apply_budget(messages, _Config({"context_token_budget": 0})) == messages


def test_over_budget_drops_oldest_history_first():
    messages = _long_history(rounds=10, chars=400)
    config = _Config({"context_token_budget": 300, "context_token_chars_per_token": 1})
    trimmed = tb.apply_budget(messages, config)

    assert len(trimmed) < len(messages)
    assert trimmed[0]["role"] == "system" and trimmed[0]["content"] == messages[0]["content"]
    assert trimmed[-1] == {"role": "user", "content": "本轮提问"}, "本轮提问必须保留"
    assert tb.estimate_tokens(trimmed, per_token_chars=1) <= 300
    # 丢的是最旧的：剩下的第一条历史应晚于原来的第一条
    remaining_history = [m for m in trimmed if m["role"] != "system"][0]
    assert remaining_history["content"] != messages[1]["content"]


def test_truncate_keeps_order():
    messages = _long_history(rounds=5, chars=200)
    trimmed, dropped = tb.truncate(messages, 300, per_token_chars=1)
    assert dropped > 0
    order = [m["content"] for m in trimmed]
    assert order == [m["content"] for m in messages if m["content"] in order]


def test_unavoidable_over_budget_does_not_crash():
    """预算比必留内容还小时不硬切（只留 warning），消息原样返回。"""
    messages = [{"role": "system", "content": "人设" * 200}, {"role": "user", "content": "问"}]
    trimmed, dropped = tb.truncate(messages, 10, per_token_chars=1)
    assert dropped == 0
    assert trimmed == messages


def test_truncate_handles_empty_and_zero_limit():
    assert tb.truncate([], 100) == ([], 0)
    messages = _long_history(rounds=1, chars=10)
    assert tb.truncate(messages, 0) == (messages, 0)


# ==================== 与组装收口打通 ====================


async def test_apply_post_assemble_applies_budget():
    runtime = _Runtime({"context_token_budget": 300, "context_token_chars_per_token": 1})
    messages = _long_history(rounds=10, chars=400)

    result = await apply_post_assemble(runtime, None, messages)
    assert result is not None
    assert len(result) < len(messages)
    assert result[-1]["content"] == "本轮提问"


async def test_apply_post_assemble_noop_when_disabled():
    runtime = _Runtime()
    messages = _long_history(rounds=3, chars=400)
    result = await apply_post_assemble(runtime, None, messages)
    assert result == messages
