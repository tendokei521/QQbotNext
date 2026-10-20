"""历史存储契约 + 外部历史导入（迁移用）。

接管组装器的插件需要**稳定可依赖**的历史读取口径，才不会跟着框架内部重构碎：

- ``get_history()``：投影视图（白名单字段、默认只取最近 10 条、滤杂散 assistant），
  直接可喂 OpenAI 风格 messages；
- ``history_snapshot()``：**未投影的原始条目深拷贝**（保留 ``base.segments`` /
  ``meta_lines``），供插件用自己的渲染器 1:1 复刻第三方格式；
- ``import_history()``：把外部（AstrBot/OpenAI 风格）历史批量搬进来，
  让框架既有组装链路也能读到迁移过来的上下文。

注意：``SessionManager`` 是**按 bot_id 的进程级单例**，每个用例必须用不同的 bot_id，
否则会串到上一个用例已经关掉的历史库。
"""

from __future__ import annotations

import itertools

from app.agent.session.manager import (
    HISTORY_ENTRY_FIELDS,
    HISTORY_ROLES,
    SessionManager,
    normalize_import_entry,
)

_counter = itertools.count()


def _sm() -> SessionManager:
    """每个用例一个全新 bot_id（SessionManager 是按 bot_id 的单例）。"""
    return SessionManager(f"bot_hist{next(_counter)}")


def _junk_assistant(sm: SessionManager, session_id: str, content: str = "") -> None:
    """直接塞一条杂散 assistant（add_message 会拒绝写空/rate. 的助手消息）。"""
    session = sm.get_session(session_id)
    session.data.history.append({"role": "assistant", "time": 1, "content": content})


# ==================== 契约常量 ====================


def test_contract_constants_are_documented():
    assert "role" in HISTORY_ENTRY_FIELDS and "base" in HISTORY_ENTRY_FIELDS
    assert HISTORY_ROLES == ("user", "assistant", "system")


# ==================== normalize_import_entry ====================


def test_normalize_external_minimal_entry():
    entry = normalize_import_entry({"role": "user", "content": "你好"})
    assert entry["role"] == "user"
    assert entry["content"] == "你好"
    assert entry["time"] > 0
    assert "base" not in entry, "没给 segments 时保持轻量形状"


def test_normalize_joins_multimodal_text_blocks():
    """AstrBot/OpenAI 的多模态 content 数组只取文本块（图片在框架里由 segments 承载）。"""
    entry = normalize_import_entry({
        "role": "user",
        "content": [
            {"type": "text", "text": "看这张"},
            {"type": "image_url", "image_url": {"url": "https://a/1.png"}},
            {"type": "text", "text": "图"},
        ],
    })
    assert entry["content"] == "看这张图"


def test_normalize_builds_base_from_segments():
    entry = normalize_import_entry({
        "role": "user",
        "content": "正文",
        "user_id": 123,
        "nickname": "甲",
        "message_id": 456,
        "group_id": 778,
        "segments": [{"type": "text", "data": {"text": "正文"}}],
        "meta_lines": ["提到了 乙", "   "],
    })
    assert entry["user_id"] == "123" and entry["message_id"] == "456"
    assert entry["base"]["group_id"] == "778"
    assert entry["base"]["sender_name"] == "甲"
    assert entry["base"]["segments"][0]["type"] == "text"
    assert entry["base"]["meta_lines"] == ["提到了 乙"], "空 meta 行丢弃"


def test_normalize_accepts_timestamp_and_time():
    assert normalize_import_entry({"role": "user", "content": "x", "timestamp": 1000})["time"] == 1000
    assert normalize_import_entry({"role": "user", "content": "x", "time": "2000"})["time"] == 2000
    assert normalize_import_entry({"role": "user", "content": "x", "time": "坏值"})["time"] > 0


def test_normalize_drops_garbage():
    assert normalize_import_entry("不是字典") is None
    assert normalize_import_entry({"content": "缺 role"}) is None
    assert normalize_import_entry({"role": "tool", "content": "角色不在白名单"}) is None
    assert normalize_import_entry({"role": "user", "content": "   "}) is None
    assert normalize_import_entry({"role": "assistant", "content": ""}) is None


def test_normalize_keeps_segments_only_entry():
    """只有 segments、没有正文的条目也要能搬（图片/表情消息）。"""
    entry = normalize_import_entry({
        "role": "user",
        "content": "",
        "segments": [{"type": "face", "data": {"id": "1"}}],
    })
    assert entry is not None and entry["content"] == ""
    assert entry["base"]["segments"][0]["type"] == "face"


# ==================== history_snapshot ====================


def test_snapshot_keeps_raw_shape_that_get_history_projects_away():
    sm = _sm()
    sm.create_session("group_1", "group", 600)
    sm.add_message(
        "group_1", "user", "你好", user_id="1", nickname="甲",
        segments=[{"type": "text", "data": {"text": "你好"}}],
    )
    # 直接塞两个 get_history 白名单外的键，验证"原始条目"不丢字段
    session = sm.get_session("group_1")
    session.data.history[0]["extra_field"] = {"k": "v"}
    session.data.history[0]["turn"] = 3

    raw = sm.history_snapshot("group_1")[0]
    assert raw["extra_field"] == {"k": "v"}, "原始条目保留白名单外字段"
    assert raw["base"]["segments"][0]["data"]["text"] == "你好"
    assert raw["base"]["text"] == "你好"

    projected = sm.get_history("group_1", limit=10)[0]
    assert "extra_field" not in projected, "get_history 做字段投影"
    assert projected["base"]["segments"][0]["data"]["text"] == "你好", "base 结构两边都在"


def test_snapshot_is_deep_copy():
    sm = _sm()
    sm.create_session("group_1", "group", 600)
    sm.add_message(
        "group_1", "user", "你好", user_id="1",
        segments=[{"type": "text", "data": {"text": "你好"}}],
    )

    snap = sm.history_snapshot("group_1")
    snap[0]["content"] = "被插件改坏了"
    snap[0]["base"]["segments"][0]["data"]["text"] = "被插件改坏了"

    again = sm.history_snapshot("group_1")
    assert again[0]["content"] == "你好", "改快照副本不该动到存储"
    assert again[0]["base"]["segments"][0]["data"]["text"] == "你好"


def test_snapshot_has_no_default_limit_but_get_history_does():
    sm = _sm()
    sm.create_session("group_1", "group", 600)
    for i in range(15):
        sm.add_message("group_1", "user", f"第{i}条")

    assert len(sm.history_snapshot("group_1")) == 15, "快照默认给全部"
    assert len(sm.get_history("group_1")) == 10, "get_history 默认只给最近 10 条"
    assert [m["content"] for m in sm.history_snapshot("group_1", limit=2)] == ["第13条", "第14条"]
    assert sm.history_snapshot("group_1", limit=0) == []


def test_snapshot_filters_junk_unless_asked():
    sm = _sm()
    sm.create_session("group_1", "group", 600)
    sm.add_message("group_1", "user", "正文")
    _junk_assistant(sm, "group_1", "")
    _junk_assistant(sm, "group_1", "rate.")

    assert [m["content"] for m in sm.history_snapshot("group_1")] == ["正文"], "默认滤掉空/杂散 assistant"
    assert len(sm.history_snapshot("group_1", include_junk=True)) == 3


def test_snapshot_missing_session_is_empty():
    assert _sm().history_snapshot("group_不存在") == []


# ==================== import_history ====================


def test_import_creates_session_and_writes_history():
    sm = _sm()
    result = sm.import_history("private_9", [
        {"role": "user", "content": "在吗"},
        {"role": "assistant", "content": "在的"},
    ], session_type="private")

    assert result == {"imported": 2, "skipped": 0, "dropped": 0, "total": 2}
    session = sm.get_session("private_9")
    assert session is not None and session.type == "private", "会话不存在时自动创建"
    assert [(m["role"], m["content"]) for m in sm.history_snapshot("private_9")] == [
        ("user", "在吗"),
        ("assistant", "在的"),
    ]


def test_import_counts_skipped_entries_without_raising():
    sm = _sm()
    result = sm.import_history("group_2", [
        {"role": "user", "content": "有效"},
        {"role": "tool", "content": "角色不支持"},
        "不是字典",
        {"role": "user", "content": "  "},
    ])
    assert result["imported"] == 1
    assert result["skipped"] == 3, "脏数据计数丢弃，不中断整批导入"
    assert result["total"] == 1


def test_import_appends_by_default_and_replaces_on_demand():
    sm = _sm()
    sm.create_session("group_3", "group", 600)
    sm.add_message("group_3", "user", "框架原本的一条")

    sm.import_history("group_3", [{"role": "user", "content": "外部一条"}])
    assert [m["content"] for m in sm.history_snapshot("group_3")] == ["框架原本的一条", "外部一条"]

    sm.import_history("group_3", [{"role": "user", "content": "全新一条"}], replace=True)
    assert [m["content"] for m in sm.history_snapshot("group_3")] == ["全新一条"]


def test_import_trims_to_max_history_messages():
    sm = _sm()
    total = SessionManager.MAX_HISTORY_MESSAGES + 5
    result = sm.import_history("group_4", [
        {"role": "user", "content": f"第{i}条"} for i in range(total)
    ])
    assert result["imported"] == total
    assert result["dropped"] == 5
    assert result["total"] == SessionManager.MAX_HISTORY_MESSAGES
    snapshot = sm.history_snapshot("group_4")
    assert snapshot[0]["content"] == "第5条", "从最旧开始丢"


def test_import_persists_across_reload():
    """导入要真的落盘：换一个会话对象/内存清空后仍能读回（迁移场景常见）。"""
    sm = _sm()
    sm.import_history("group_5", [
        {"role": "user", "content": "迁移过来的一句"},
        {"role": "assistant", "content": "收到"},
    ])
    session = sm.get_session("group_5")
    sm.history.save_session(session)

    sm.forget_session("group_5")
    assert sm.get_session("group_5") is None

    restored = sm.create_session("group_5", "group", 600)
    sm.restore_session_from_archive(restored, "group_5")
    assert [m["content"] for m in sm.history_snapshot("group_5")] == ["迁移过来的一句", "收到"]


def test_imported_history_reaches_framework_assembler():
    """端到端意义：搬进来的历史必须能被框架既有组装链路读到。"""
    sm = _sm()
    sm.import_history("group_6", [
        {"role": "user", "content": "昨天的约定"},
        {"role": "assistant", "content": "记得"},
    ])

    projected = sm.get_history("group_6", limit=50)
    assert [m["role"] for m in projected] == ["user", "assistant"]
    assert projected[0]["content"] == "昨天的约定"
    assert projected[0]["time"] > 0, "没有时间戳的条目会被补上当前时间"
