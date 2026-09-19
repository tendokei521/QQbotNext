"""历史渲染特征化测试（重构安全网）。

背景：会话历史与在线背景是**两套渲染器**——会话历史走 ``format_history_for_llm``
（读冻结文本、不接补全登记），在线背景走 ``format_online_history`` / ``extract_msg_text``
（读 OneBot 原始段、接补全登记）。重构要把它们统一到 ``history_model.render_history_entry``，
因此这里先把**两类渲染的现有输出逐字锁住**：

- 单行形态 ``MM-DD HH:MM 昵称(QQ): 正文``（会话历史 / 群聊背景）
- 多行形态 ``(时间：…)\\n发送者：…\\n发送了：正文``（增强块，历史里原样保留）
- 未展开标记与已展开标记（``【未展开:引用456】`` / ``【已展开:引用456 → …】``）
- 旧数据（只有 content、没有结构化字段）必须仍可读
"""

from __future__ import annotations

from app.llm.group_context import (
    EXPANDED_REPLY,
    UNRESOLVED_FORWARD_ID,
    UNRESOLVED_REPLY,
    ExpandedRefs,
    extract_msg_text,
    format_history_for_llm,
    format_online_history,
)

# ---------- 会话历史侧（冻结文本） ----------

HISTORY_GROUP = [
    {"role": "user", "content": "你好", "time": 1788342260, "user_id": "20002", "nickname": "小明"},
    {"role": "assistant", "content": "你好呀"},
    {
        "role": "user",
        "content": "(时间：2026-08-20 21:10:02)\n(当前群号: 466052056)\n发送者：老师(1901691195)\n发送了：学费怎么交",
        "time": 1788342400,
        "user_id": "1901691195",
        "nickname": "老师，今年的学费也是一次性交吗",
    },
]


def test_session_history_group_single_line_and_assistant_plain():
    rendered = format_history_for_llm(HISTORY_GROUP, is_private=False)
    assert rendered[0]["content"].endswith("小明(20002): 你好")
    # assistant 不加时间/「我:」前缀（防模型模仿）
    assert rendered[1] == {"role": "assistant", "content": "你好呀"}
    # 增强块：未开启归一化时原样保留（句子型昵称不脱敏）
    assert rendered[2]["content"].startswith("(时间：2026-08-20 21:10:02)")


def test_session_history_private_uses_other_tag():
    rendered = format_history_for_llm(HISTORY_GROUP[:2], is_private=True)
    assert rendered[0]["content"].endswith("对方: 你好")


def test_session_history_mask_nickname_rewrites_sentence_like_nickname():
    rendered = format_history_for_llm(HISTORY_GROUP[:1], is_private=False, mask_nickname=True)
    assert "用户名" not in rendered[0]["content"]
    assert "小明(20002)" in rendered[0]["content"]


def test_session_history_normalize_enhanced_collapses_sections_to_label_line():
    """归一化把「发送者/发送了」两行并成 ``昵称(QQ): 正文``，时间行与群号行保留。"""
    rendered = format_history_for_llm(
        HISTORY_GROUP[2:], is_private=False, normalize_enhanced=True
    )
    lines = rendered[0]["content"].splitlines()
    assert lines[0] == "(时间：2026-08-20 21:10:02)"
    assert lines[1] == "老师(1901691195): 学费怎么交"
    assert lines[-1] == "(当前群号: 466052056)"


def test_session_history_empty_and_legacy_without_fields():
    assert format_history_for_llm([], is_private=False) == []
    legacy = [{"role": "user", "content": "只有正文"}]
    rendered = format_history_for_llm(legacy, is_private=False)
    assert rendered[0]["content"].endswith(": 只有正文")


# ---------- 在线背景侧（OneBot 原始段） ----------

ONLINE = [
    {
        "time": 1788342260,
        "message_id": 1001,
        "sender": {"user_id": 20002, "nickname": "小明", "card": ""},
        "message": [{"type": "text", "data": {"text": "在吗"}}],
    },
    {
        "time": 1788342300,
        "message_id": 1002,
        "sender": {"user_id": 30003, "nickname": "小红", "card": "小red"},
        "message": [
            {"type": "reply", "data": {"id": "456"}},
            {"type": "text", "data": {"text": "收到"}},
        ],
    },
    {
        "time": 1788342360,
        "message_id": 1003,
        "sender": {"user_id": 778, "nickname": "我", "card": ""},
        "message": [{"type": "image", "data": {"url": "https://a/1.png"}}],
    },
]


def test_online_history_group_labels_and_self_tag():
    text = format_online_history(ONLINE, self_ids={"778"})
    lines = text.splitlines()
    assert lines[0].endswith("小明(20002): 在吗")
    assert lines[1].endswith("小red(30003): [引用]收到")
    assert lines[2].endswith("我: [图片]")


def test_online_history_private_hides_nickname():
    text = format_online_history(ONLINE[:1], self_ids=set(), is_private=True)
    assert text.endswith("对方: 在吗")


def test_online_history_without_id_hides_user_id():
    text = format_online_history(ONLINE[:1], self_ids=set(), include_user_id=False)
    assert text.endswith("小明: 在吗")


def test_online_history_mark_unresolved_marks_actionable_gaps():
    text = format_online_history(ONLINE, self_ids={"778"}, mark_unresolved=True)
    assert UNRESOLVED_REPLY.format(id="456") in text
    assert UNRESOLVED_FORWARD_ID.format(id="1003") not in text  # 图片不是可展开缺口
    assert "[图片]" in text


def test_online_history_expanded_registry_upgrades_marker():
    # ExpandedRefs 只分 messages（消息 id → 摘要）与 users（QQ → 昵称）两类，避免数字串号
    refs = ExpandedRefs(messages={"456": "上一条说的学费", "1004": "转发摘要"})
    text = format_online_history(ONLINE, self_ids={"778"}, mark_unresolved=True, expanded=refs)
    assert EXPANDED_REPLY.format(id="456", summary="上一条说的学费") in text
    # 已展开的合并转发登记（本条无转发段，用直接渲染验证格式）
    msg = extract_msg_text(
        [{"type": "forward", "data": {"id": "inner"}}], None, True, "1004", refs
    )
    assert msg == "【已展开:合并转发1004 → 转发摘要】"


def test_online_history_max_content_truncates():
    long_msg = [{
        "time": 1, "message_id": 1, "sender": {"user_id": 9, "nickname": "长"},
        "message": [{"type": "text", "data": {"text": "x" * 50}}],
    }]
    text = format_online_history(long_msg, max_content=10)
    assert text.endswith("x" * 10 + "...")


def test_online_history_enhanced_block_kept_without_outer_prefix():
    """已自带「发送者/发送了」的增强块不再套外层前缀（与会话历史同一规则）。"""
    msg = [{
        "time": 1, "message_id": 1,
        "sender": {"user_id": 9, "nickname": "老师，今年的学费也是一次性交吗"},
        "message": [{"type": "text", "data": {"text": "发送者：老师(9)\n发送了：你好"}}],
    }]
    text = format_online_history(msg)
    assert text.startswith("发送者：老师(9)")


# ---------- 旧数据可读性（生产库形状） ----------

def test_legacy_rows_without_structured_fields_are_readable():
    """生产库里的历史行只有 content/role/time（user_id/nickname 可能缺失）。"""
    rows = [
        {"role": "user", "content": "(时间：2026-09-19 19:41:48)\n发送者：用户1901691195\n发送了：在吗", "time": 1},
        {"role": "user", "content": "裸正文", "time": 2},
        {"role": "assistant", "content": "回复", "time": 3},
    ]
    rendered = format_history_for_llm(rows, is_private=False)
    assert rendered[0]["content"].startswith("(时间：2026-09-19 19:41:48)")
    assert rendered[1]["content"].endswith(": 裸正文")
    assert rendered[2] == {"role": "assistant", "content": "回复"}


def test_online_history_handles_missing_sender_and_empty_message():
    rows = [
        {"time": 1, "message_id": 1, "message": [{"type": "text", "data": {"text": "hi"}}]},
        {"time": 2, "message_id": 2, "sender": {"user_id": 9}, "message": []},
    ]
    text = format_online_history(rows)
    assert "hi" in text
    assert len(text.splitlines()) == 1  # 空消息不进背景
