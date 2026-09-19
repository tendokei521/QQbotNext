"""history_model 结构化渲染测试（H1）。

锁住「基础信息 + 轮次信息」模型的渲染行为：

- 结构化条目（``base``）走 base 字段渲染，不再依赖冻结文本；
- 旧条目（只有 ``content``）必须与结构化前逐字一致（由 test_llm_history_render 锁）；
- ``turn.expansions`` 参与渲染（本轮先只验证模型与渲染入口，回写在 H4）；
- 换行统一为 ``\\n``（CRLF 会让"增强块"判定失效并多套一层前缀）。
"""

from __future__ import annotations

from app.llm.history_model import (
    BaseInfo,
    HistoryEntry,
    TurnInfo,
    is_enhanced_content,
    normalize_enhanced_content,
    render_history_entry,
)


def _entry(**base_kw) -> HistoryEntry:
    base = {"time": 1788342260, "sender_id": "20002", "sender_name": "小明", "text": "你好"}
    base.update(base_kw)
    return HistoryEntry(role="user", message_id="1001", base=BaseInfo(**base))


# ---------- 结构化渲染 ----------


def test_structured_entry_renders_single_line():
    assert render_history_entry(_entry()) == "09-02 17:44 小明(20002): 你好"


def test_structured_entry_private_uses_other_tag():
    text = render_history_entry(_entry(), is_private=True)
    assert text.endswith("对方: 你好")


def test_structured_entry_self_id_renders_self_tag():
    text = render_history_entry(_entry(), self_ids={"20002"})
    assert text.endswith("我: 你好")


def test_structured_assistant_never_gets_prefix():
    entry = HistoryEntry(role="assistant", base=BaseInfo(time=1, text="回复内容"))
    assert render_history_entry(entry) == "回复内容"


def test_structured_entry_include_time_and_user_id_switches():
    assert render_history_entry(_entry(), include_time=False) == "小明(20002): 你好"
    assert render_history_entry(_entry(), include_user_id=False) == "09-02 17:44 小明: 你好"


def test_structured_mask_nickname_protects_sentence_like_names():
    text = render_history_entry(
        _entry(sender_name="老师，今年的学费也是一次性交吗"), mask_nickname=True
    )
    assert text.endswith("用户20002: 你好")


def test_structured_empty_text_is_dropped():
    assert render_history_entry(HistoryEntry(role="user", base=BaseInfo(sender_id="1"))) is None


def test_structured_render_content_callback_can_enrich():
    """富渲染钩子：正文可由回调生成（H4 接补全/图片展开用）。"""
    entry = _entry()

    def _render(e):
        return "富渲染正文"

    assert render_history_entry(entry, render_content=_render).endswith(": 富渲染正文")
    assert render_history_entry(entry, render_content=lambda e: "") is None


# ---------- 轮次信息模型 ----------


def test_turn_info_add_is_idempotent_per_kind_and_ref():
    turn = TurnInfo()
    turn.add("reply", "456", "学费那题", source="expand_message")
    turn.add("reply", "456", "更新的摘要")
    turn.add("user", "123", "三哥")
    assert len(turn.expansions) == 2
    assert turn.find("reply", "456")["summary"] == "更新的摘要"
    assert turn.find("reply", "456")["source"] == "expand_message"
    assert [e["ref"] for e in turn.by_kind("user")] == ["123"]


def test_turn_info_ignores_empty_payload():
    turn = TurnInfo()
    turn.add("reply", "", "无引用")
    turn.add("reply", "456", "")
    assert turn.expansions == []


def test_entry_roundtrip_keeps_base_turn_and_message_id():
    entry = _entry()
    entry.turn.add("forward", "1004", "转发摘要", content="完整内容")
    data = entry.to_dict()
    again = HistoryEntry.from_dict(data)
    assert again.message_id == "1001"
    assert again.base.text == "你好"
    assert again.turn.find("forward", "1004")["content"] == "完整内容"


# ---------- 旧数据兼容 ----------


def test_legacy_row_fields_backfill_base_but_text_stays_legacy():
    row = {"role": "user", "content": "裸正文", "time": 1788342260, "user_id": "20002", "nickname": "小明"}
    entry = HistoryEntry.from_dict(row)
    assert entry.legacy_text == "裸正文"
    assert entry.base.sender_name == "小明"
    assert render_history_entry(row) == "09-02 17:44 小明(20002): 裸正文"


def test_legacy_enhanced_block_kept_verbatim():
    row = {"role": "user", "content": "发送者：小明(20002)\n发送了：你好", "time": 1}
    assert render_history_entry(row) == "发送者：小明(20002)\n发送了：你好"


def test_crlf_enhanced_block_is_detected_after_normalization():
    """CRLF 会让分节正则失配 → 先统一换行再判定，避免多套一层前缀。"""
    crlf = "发送者：小明(20002)\r\n发送了：你好"
    assert is_enhanced_content(crlf)
    assert render_history_entry({"role": "user", "content": crlf, "time": 1}) == (
        "发送者：小明(20002)\n发送了：你好"
    )


def test_normalize_enhanced_keeps_time_and_meta_lines():
    content = "(时间：2026-08-20 21:10:02)\n发送者：老师(9)\n发送了：学费怎么交\n(当前群号: 466)"
    out = normalize_enhanced_content(content)
    assert out.splitlines() == [
        "(时间：2026-08-20 21:10:02)",
        "老师(9): 学费怎么交",
        "(当前群号: 466)",
    ]
