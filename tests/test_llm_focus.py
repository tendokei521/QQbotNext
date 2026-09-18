"""会话焦点表（app.llm.focus）与"已展开"状态迁移测试。

设计基准：docs/referent-resolution-design.md。

要解决的真实问题（group_466052056 / 2026-09-18 21:37）：用户说"那你能做到消息里的那个
样子吗"是**纯回指**句，而上一轮取回过的内容因为工具结果不进会话历史而彻底消失，
模型只能去背景块里猜一个最显眼的 id。焦点表负责把"刚才在讨论什么"留在它眼前。
"""

from __future__ import annotations

from app.llm import focus


def setup_function(_fn):
    focus.clear_all()


# ---------- 登记与显著性 ----------


def test_note_and_items_sorted_by_salience():
    focus.begin_turn("b", "group_1")
    focus.note("b", "group_1", "111", kind="message", label="消息111", source="window")
    focus.note("b", "group_1", "222", kind="message", label="消息222", source="reply")

    refs = [i.ref for i in focus.items("b", "group_1")]

    assert refs == ["222", "111"]  # 显式回复权重更高


def test_older_turn_decays():
    focus.begin_turn("b", "g")           # turn 1
    focus.note("b", "g", "111", source="expand")
    for _ in range(4):                   # turn 2..5
        focus.begin_turn("b", "g")
    focus.note("b", "g", "222", source="expand")

    items = focus.items("b", "g")

    assert [i.ref for i in items] == ["222", "111"]
    assert focus.score(items[0], 5) > focus.score(items[1], 5)


def test_summary_only_overwritten_by_non_empty():
    focus.note("b", "g", "111", summary="第一次取到的摘要")
    focus.note("b", "g", "111", summary="")

    assert focus.summary_of("b", "g", "111") == "第一次取到的摘要"


def test_source_not_downgraded_by_weaker_evidence():
    focus.note("b", "g", "111", source="reply")
    focus.note("b", "g", "111", source="window")

    assert focus.get("b", "g", "111").source == "reply"


def test_sessions_are_isolated_by_bot_and_session():
    focus.note("b1", "g", "111", summary="A")
    focus.note("b2", "g", "111", summary="B")

    assert focus.summary_of("b1", "g", "111") == "A"
    assert focus.summary_of("b2", "g", "111") == "B"
    assert focus.summary_of("b1", "other", "111") == ""


def test_empty_ref_is_ignored():
    assert focus.note("b", "g", "") is None
    assert focus.items("b", "g") == []


# ---------- TTL / 上限 ----------


def test_ttl_eviction():
    import time

    focus.note("b", "g", "111", summary="A", ttl=0.01)
    assert focus.get("b", "g", "111") is not None  # 未过期

    time.sleep(0.02)
    assert focus.get("b", "g", "111") is None  # 读取时做过期淘汰


def test_read_time_pruning_respects_max_items():
    focus.begin_turn("b", "g")
    for i in range(focus.DEFAULT_MAX_ITEMS + 5):
        focus.note("b", "g", f"m{i}", source="window", max_items=999)

    assert len(focus.items("b", "g")) <= focus.DEFAULT_MAX_ITEMS


def test_max_items_keeps_strongest():
    focus.begin_turn("b", "g")
    for i in range(6):
        focus.note("b", "g", f"w{i}", source="window", max_items=100)
    focus.note("b", "g", "important", source="reply", max_items=3)

    refs = {i.ref for i in focus.items("b", "g")}

    assert "important" in refs
    assert len(refs) <= 3


# ---------- 已展开映射 ----------


def test_expanded_refs_split_by_kind():
    focus.note("b", "g", "576048059", kind="message", summary="B站视频", source="expand")
    focus.note("b", "g", "123", kind="user", summary="三哥", source="expand")
    focus.note("b", "g", "999", kind="message", source="window")  # 没摘要 → 不算已展开

    refs = focus.expanded_refs("b", "g")

    assert refs["messages"] == {"576048059": "B站视频"}
    assert refs["users"] == {"123": "三哥"}


# ---------- 焦点行 ----------


def test_focus_lines_render_relative_turn_and_source():
    focus.begin_turn("b", "g")           # turn 1
    focus.note("b", "g", "576048059", kind="message", label="合并转发576048059",
               summary="成人向对话记录", source="expand")
    focus.begin_turn("b", "g")           # turn 2
    focus.note("b", "g", "1000165352", kind="message", label="合并转发1000165352",
               summary="B站《学校的8种违法行为》", source="window")

    text = focus.focus_lines("b", "g")

    assert "当前对话焦点" in text
    assert "合并转发1000165352" in text and "本轮" in text
    assert "合并转发576048059" in text and "上一轮" in text
    assert "B站《学校的8种违法行为》" in text


def test_focus_lines_empty_without_focus():
    assert focus.focus_lines("b", "g") == ""


def test_focus_lines_respects_limit_and_budget():
    focus.begin_turn("b", "g")
    for i in range(5):
        focus.note("b", "g", f"m{i}", label=f"消息m{i}", summary="x" * 200, source="expand")

    text = focus.focus_lines("b", "g", limit=1, max_chars=50)

    assert text.count("\n") == 1  # 只有标题 + 1 条


# ---------- 清理 ----------


def test_clear_scopes():
    focus.note("b1", "g", "1")
    focus.note("b2", "g", "1")

    focus.clear("b1", "g")
    assert focus.items("b1", "g") == []
    assert len(focus.items("b2", "g")) == 1

    focus.clear("b2")
    assert focus.items("b2", "g") == []
