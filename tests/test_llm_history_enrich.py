"""H4 补全登记测试：信息一旦查看过，就留在会话历史里。

验证链路：

    工具取回（ledger 记账）→ 请求收尾 commit → 持久登记
      → 下一轮渲染历史：占位替换为【已展开:… → 摘要】，取回的单条消息作为附加块

与 ``focus`` 的分工：focus 是进程内有界注意力（不落盘），本模块是跟随会话的持久事实。
"""

from __future__ import annotations

import importlib

import pytest

from app.llm import history_enrich


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """每个用例独立的数据目录 + 清空内存缓存。"""
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    monkeypatch.setattr(history_enrich, "_STORE", None, raising=False)
    history_enrich._LEDGERS.clear()
    yield
    history_enrich._STORE = None
    history_enrich._LEDGERS.clear()


# ---------- 登记读写 ----------


def test_remember_and_lookup_roundtrip():
    history_enrich.remember("group_1", 778, items=[
        {"kind": "reply", "ref": "456", "summary": "上一条在说学费"},
    ])
    item = history_enrich.lookup("group_1", 778, "456")
    assert item["summary"] == "上一条在说学费"
    assert item["kind"] == "reply"


def test_remember_persists_across_reload():
    history_enrich.remember("group_1", 778, items=[{"kind": "forward", "ref": "1004", "summary": "转发摘要"}])
    # 模拟进程重启：清掉内存缓存，从磁盘读回
    history_enrich._STORE = None
    assert history_enrich.lookup("group_1", 778, "1004")["summary"] == "转发摘要"


def test_remember_evicts_oldest_beyond_cap(monkeypatch):
    monkeypatch.setattr(history_enrich, "MAX_REFS_PER_SESSION", 2)
    history_enrich.remember("group_1", 778, items=[{"kind": "reply", "ref": "1", "summary": "a"}])
    history_enrich.remember("group_1", 778, items=[{"kind": "reply", "ref": "2", "summary": "b"}])
    history_enrich.remember("group_1", 778, items=[{"kind": "reply", "ref": "3", "summary": "c"}])
    refs = set(history_enrich.load_for("group_1", 778))
    assert len(refs) == 2 and "1" not in refs


def test_sessions_are_isolated():
    history_enrich.remember("group_1", 778, items=[{"kind": "reply", "ref": "456", "summary": "a"}])
    assert history_enrich.lookup("group_2", 778, "456") is None
    assert history_enrich.lookup("group_1", 999, "456") is None


# ---------- 标记替换 ----------


@pytest.mark.parametrize("raw,expected", [
    ("[引用456]你好", "【已展开:引用456 → 学费那题】你好"),
    ("【未展开:引用456】", "【已展开:引用456 → 学费那题】"),
    ("[合并转发1004]", "【已展开:合并转发1004 → 转发摘要】"),
    ("【未展开:合并转发1004】", "【已展开:合并转发1004 → 转发摘要】"),
])
def test_replace_markers_upgrades_placeholders(raw, expected):
    history_enrich.remember("group_1", 778, items=[
        {"kind": "reply", "ref": "456", "summary": "学费那题"},
        {"kind": "forward", "ref": "1004", "summary": "转发摘要"},
    ])
    assert history_enrich.replace_markers(raw, "group_1", 778) == expected


def test_replace_markers_leaves_unregistered_alone():
    history_enrich.remember("group_1", 778, items=[{"kind": "reply", "ref": "456", "summary": "x"}])
    assert history_enrich.replace_markers("[引用999]", "group_1", 778) == "[引用999]"


def test_replace_markers_upgrades_at_with_known_username():
    history_enrich.remember("group_1", 778, items=[{"kind": "user", "ref": "30003", "summary": "小红"}])
    assert history_enrich.replace_markers("@30003 看这个", "group_1", 778) == "@小红(30003) 看这个"


def test_replace_markers_without_registry_is_noop():
    assert history_enrich.replace_markers("[引用456]", "group_1", 778) == "[引用456]"


# ---------- 附加块 ----------


def test_supplementary_blocks_returns_message_content_not_reply():
    history_enrich.remember("group_1", 778, items=[
        {"kind": "message", "ref": "900", "summary": "短摘要", "content": "完整内容在这里"},
        {"kind": "reply", "ref": "456", "summary": "引用摘要"},
    ])
    entry = {
        "role": "user",
        "message_id": "900",
        "base": {"segments": [{"type": "reply", "data": {"id": "456"}}]},
    }
    blocks = history_enrich.supplementary_blocks("group_1", 778, entry)
    assert blocks == [{"role": "user", "content": "【已取回:消息900】完整内容在这里"}]


def test_entry_refs_extracts_reply_and_forward_carrier_id():
    entry = {
        "message_id": "1004",
        "base": {"segments": [
            {"type": "reply", "data": {"id": "456"}},
            {"type": "forward", "data": {}},
        ]},
    }
    refs = history_enrich.entry_refs(entry)
    assert "456" in refs and "1004" in refs


# ---------- 与渲染接线 ----------


def _structured_entry(text: str, message_id: str = "1001") -> dict:
    return {
        "role": "user",
        "message_id": message_id,
        "time": 1788342260,
        "base": {
            "time": 1788342260, "sender_id": "20002", "sender_name": "小明",
            "text": text, "segments": [],
        },
    }


def test_format_history_for_llm_applies_registry():
    from app.llm.group_context import format_history_for_llm

    history_enrich.remember("group_1", 778, items=[
        {"kind": "reply", "ref": "456", "summary": "学费那题"},
        {"kind": "message", "ref": "900", "summary": "短摘要", "content": "完整内容"},
    ])
    history = [
        _structured_entry("[引用456] 这个怎么弄"),
        _structured_entry("看过了", message_id="900"),
    ]
    rendered = format_history_for_llm(history, bot_id=778, session_id="group_1")
    assert "【已展开:引用456 → 学费那题】" in rendered[0]["content"]
    # 取回过的单条消息作为附加块插在对应条目之后，而不是塞进用户正文
    assert rendered[1]["role"] == "user" and "看过了" in rendered[1]["content"]
    assert rendered[2]["content"] == "【已取回:消息900】完整内容"


def test_format_history_for_llm_without_registry_unchanged():
    from app.llm.group_context import format_history_for_llm

    rendered = format_history_for_llm([_structured_entry("[引用456] 这个怎么弄")])
    assert rendered[0]["content"].endswith(": [引用456] 这个怎么弄")


# ---------- 记账 → 提交 ----------


def test_ledger_record_and_commit():
    ledger = history_enrich.ledger_for(778, "group_1")
    ledger.record("reply", "456", "摘要", content="完整", source="expand_message")
    assert ledger  # 非空
    assert history_enrich.commit(778, "group_1", trigger_message_id="1001") == 1
    item = history_enrich.lookup("group_1", 778, "456")
    assert item["trigger_message_id"] == "1001"
    assert item["content"] == "完整"
    # 提交后记账本清空
    assert history_enrich.commit(778, "group_1") == 0


def test_ledger_ignores_empty_records():
    ledger = history_enrich.ledger_for(778, "group_1")
    ledger.record("reply", "", "无 ref")
    ledger.record("reply", "456", "")
    assert not ledger
    assert history_enrich.commit(778, "group_1") == 0


def test_expansion_ledger_records_from_tool_context():
    """工具侧只依赖 ToolContext.extra 里的记账本，不直接碰存储。"""
    from app.llm.context_tools import _record_expansion

    class _Ctx:
        def __init__(self):
            self.extra = {"expansion_ledger": history_enrich.ledger_for(778, "group_1")}

    _record_expansion(_Ctx(), "forward", "1004", summary="转发摘要", content="内容", source="get_forward_msg")
    assert history_enrich.commit(778, "group_1", trigger_message_id="1001") == 1
    assert history_enrich.lookup("group_1", 778, "1004")["kind"] == "forward"


def test_record_expansion_without_ledger_is_silent():
    from app.llm.context_tools import _record_expansion

    class _Ctx:
        extra: dict = {}

    _record_expansion(_Ctx(), "reply", "456", summary="x")  # 不应抛异常


def test_module_reload_keeps_disk_data(tmp_path):
    history_enrich.remember("group_1", 778, items=[{"kind": "reply", "ref": "1", "summary": "a"}])
    reloaded = importlib.reload(history_enrich)
    assert reloaded.lookup("group_1", 778, "1")["summary"] == "a"
