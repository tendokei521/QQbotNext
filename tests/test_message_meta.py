"""消息元信息格式消歧测试：单行脱敏格式与旧格式归一化。"""

from app.llm.group_context import (
    _is_enhanced_context,
    _mask_enhanced_content,
    _normalize_enhanced_content,
    format_history_for_llm,
    safe_nickname,
    safe_sender_label,
)
from app.llm.prompt import MESSAGE_META_INSTRUCTION, build_messages


def test_is_enhanced_context_old_format():
    assert _is_enhanced_context("(时间：2026-08-20 21:10:02)\n发送者：老师(1901691195)\n发送了：你好")
    assert _is_enhanced_context("发送者：老师(1901691195)\n发送了：你好")


def test_is_enhanced_context_legacy_new_format():
    assert _is_enhanced_context("(时间：2026-08-20 21:10:02)\n发送者昵称：老师(1901691195)\n消息正文：你好")
    assert _is_enhanced_context("发送者昵称：老师(1901691195)\n消息正文：你好")


def test_is_enhanced_context_rejects_plain_text():
    assert not _is_enhanced_context("老师，今年的学费也是一次性交吗")
    assert not _is_enhanced_context("前缀发送者：你好")  # 非行首不算增强块
    assert not _is_enhanced_context("前缀发送了：你好")


def test_safe_nickname():
    assert safe_nickname("Iyiy", "2934350679") == "Iyiy"
    assert safe_nickname("老师，今年的学费也是一次性交吗", "1901691195") == "用户1901691195"
    assert safe_nickname("群耄耋，时不时乱哈", "2016494636") == "用户2016494636"
    assert safe_nickname("", "123") == "用户123"


def test_safe_sender_label():
    assert safe_sender_label("Iyiy(2934350679)") == "Iyiy(2934350679)"
    assert safe_sender_label("老师，今年的学费也是一次性交吗(1901691195)") == "用户1901691195"


def test_build_messages_injects_meta_instruction_when_enabled():
    messages = build_messages(
        system_prompt="你是一个助手。",
        user_text="用户1901691195: 你好",
        message_meta_instruction=MESSAGE_META_INSTRUCTION,
    )
    assert any(
        m["role"] == "system" and m["content"] == MESSAGE_META_INSTRUCTION
        for m in messages
    )
    assert any("绝对不要用“用户<QQ>”“用户A”等代号直接称呼对方" in m["content"] for m in messages)


def test_build_messages_skips_meta_instruction_by_default():
    messages = build_messages(system_prompt="你是一个助手。", user_text="你好")
    assert not any("消息格式说明" in m["content"] for m in messages)


def test_normalize_enhanced_content_old_to_single_line():
    old = "发送者：老师，今年的学费也是一次性交吗(1901691195)\n发送了：你好"
    normalized = _normalize_enhanced_content(old)
    assert normalized == "用户1901691195: 你好"
    assert "发送者：" not in normalized
    assert "发送了：" not in normalized


def test_normalize_enhanced_content_keeps_plain_single_line():
    new = "用户1901691195: 你好"
    assert _normalize_enhanced_content(new) == new


def test_mask_enhanced_content_keeps_old_format_but_masks_sender():
    old = "发送者：老师，今年的学费也是一次性交吗(1901691195)\n发送了：你好"
    masked = _mask_enhanced_content(old)
    assert masked == "发送者：用户1901691195\n发送了：你好"
    assert "学费" not in masked


def test_mask_enhanced_content_masks_single_line_sender():
    single = "老师，今年的学费也是一次性交吗(1901691195): 你好"
    masked = _mask_enhanced_content(single)
    assert masked == "用户1901691195: 你好"
    assert "学费" not in masked


def test_format_history_for_llm_masks_enhanced_without_normalize():
    history = [{
        "role": "user",
        "content": "发送者：老师，今年的学费也是一次性交吗(1901691195)\n发送了：你好",
    }]
    rendered = format_history_for_llm(
        history,
        is_private=False,
        normalize_enhanced=False,
        mask_nickname=True,
    )
    assert rendered[0]["content"] == "发送者：用户1901691195\n发送了：你好"
    assert "学费" not in rendered[0]["content"]
