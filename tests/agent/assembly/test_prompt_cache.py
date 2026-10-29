"""提示词前缀稳定 + Anthropic 显式缓存标记（Q5 KV cache / prompt caching）。

锁定三件事：

1. **指纹确定性**：同一输入多次调用同值；改一个字符就变；与 dict 插入顺序无关；
2. **前缀判定**：只有尾部追加才算稳定，改中间 / 缩短 / 换 role 都不稳定；
3. **缓存标记**：开关打开时 Anthropic payload 的 system 最后一块带 cache_control，
   关闭时回到改造前的纯字符串形态，且没有 system 时干脆不带该字段。
"""

from __future__ import annotations

from app.agent.assembly.prompt_cache import (
    is_prefix_stable,
    stable_messages_digest,
    system_prefix_fingerprint,
)
from app.agent.providers.anthropic import AnthropicProvider

SYSTEM_ONE = "你是群聊里的助手，回答简短自然。"
SYSTEM_TWO = "行为规则：短句、不刷屏、不代替用户说话。"


def _messages() -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM_ONE},
        {"role": "system", "content": SYSTEM_TWO},
        {"role": "user", "content": "今天吃什么？"},
        {"role": "assistant", "content": "看你想吃辣还是清淡。"},
    ]


# ==================== 1. 指纹确定性 ====================


def test_system_fingerprint_is_deterministic():
    first = system_prefix_fingerprint(_messages())
    second = system_prefix_fingerprint(_messages())
    assert first == second
    assert len(first) == 16
    assert all(ch in "0123456789abcdef" for ch in first)


def test_system_fingerprint_only_depends_on_system_segments():
    base = system_prefix_fingerprint(_messages())
    changed_user = _messages()
    changed_user[-1]["content"] = "看你想吃火锅还是烧烤。"
    assert system_prefix_fingerprint(changed_user) == base


def test_system_fingerprint_changes_with_a_single_character():
    changed = _messages()
    changed[0]["content"] = SYSTEM_ONE.replace("简短", "简洁")
    assert system_prefix_fingerprint(changed) != system_prefix_fingerprint(_messages())


def test_system_fingerprint_is_order_sensitive():
    swapped = _messages()
    swapped[0], swapped[1] = swapped[1], swapped[0]
    assert system_prefix_fingerprint(swapped) != system_prefix_fingerprint(_messages())


def test_system_fingerprint_accepts_content_blocks():
    """纯文本与「一个 text 块」是同一种语义 → 同指纹。"""
    blocks = [
        {"role": "system", "content": [{"type": "text", "text": SYSTEM_ONE}]},
        {"role": "system", "content": [{"type": "input_text", "text": SYSTEM_TWO}]},
    ]
    assert system_prefix_fingerprint(blocks) == system_prefix_fingerprint(_messages()[:2])


def test_messages_digest_is_deterministic_and_ignores_dict_key_order():
    digest = stable_messages_digest(_messages())
    assert digest == stable_messages_digest(_messages())
    reordered_keys = [
        {"content": SYSTEM_ONE, "role": "system"},
        {"content": SYSTEM_TWO, "role": "system"},
        {"content": "今天吃什么？", "role": "user"},
        {"content": "看你想吃辣还是清淡。", "role": "assistant"},
    ]
    assert stable_messages_digest(reordered_keys) == digest


def test_messages_digest_changes_when_a_byte_changes():
    changed = _messages()
    changed[2]["content"] = "今天吃啥？"
    assert stable_messages_digest(changed) != stable_messages_digest(_messages())


# ==================== 2. 前缀判定 ====================


def test_prefix_is_stable_when_only_appending():
    prev = _messages()
    assert is_prefix_stable(prev, prev) is True
    assert is_prefix_stable(prev, prev + [{"role": "user", "content": "要辣的吗？"}]) is True


def test_prefix_is_unstable_when_middle_changes():
    prev = _messages()
    cur = [dict(message) for message in prev]
    cur[1] = {"role": "system", "content": SYSTEM_TWO + "（补充）"}
    assert is_prefix_stable(prev, cur) is False


def test_prefix_is_unstable_when_shrinking():
    prev = _messages()
    assert is_prefix_stable(prev, prev[:-1]) is False


def test_prefix_with_empty_history_is_stable():
    assert is_prefix_stable([], _messages()) is True
    assert is_prefix_stable(None, None) is True


def test_prefix_comparison_includes_role():
    prev = [{"role": "user", "content": "hi"}]
    assert is_prefix_stable(prev, [{"role": "assistant", "content": "hi"}]) is False


# ==================== 3. Anthropic 显式缓存标记 ====================


def _payload(provider: AnthropicProvider) -> dict:
    return provider._build_payload(
        _messages(),
        model="claude-3-5-sonnet-test",
        temperature=0.7,
        max_tokens=256,
    )


def test_anthropic_marks_last_system_block_when_cache_enabled():
    provider = AnthropicProvider({"api_key": "K", "prompt_cache_enable": True})
    system = _payload(provider)["system"]
    assert isinstance(system, list) and system, "开启缓存时 system 必须是块数组"
    last = system[-1]
    assert last["type"] == "text"
    assert last["cache_control"] == {"type": "ephemeral"}
    # 多段 system 仍按序合并成同一段文本（与关闭时内容一致）
    assert last["text"] == f"{SYSTEM_ONE}\n{SYSTEM_TWO}"


def test_anthropic_omits_cache_marker_when_disabled():
    provider = AnthropicProvider({"api_key": "K", "prompt_cache_enable": False})
    payload = _payload(provider)
    assert payload["system"] == f"{SYSTEM_ONE}\n{SYSTEM_TWO}"
    assert "cache_control" not in str(payload)


def test_anthropic_cache_is_on_by_default():
    provider = AnthropicProvider({"api_key": "K"})
    system = _payload(provider)["system"]
    assert isinstance(system, list)
    assert system[-1]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_without_system_has_no_system_field():
    provider = AnthropicProvider({"api_key": "K"})
    payload = provider._build_payload(
        [{"role": "user", "content": "hi"}],
        model="claude-x",
        temperature=0.7,
        max_tokens=32,
    )
    assert "system" not in payload
