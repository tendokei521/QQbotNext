"""群聊环境块的装配取值测试：降级、去重、窗口、开关、我的动作。"""

from __future__ import annotations

import time
from types import SimpleNamespace

from app.llm.group_log.context import build_context_text
from app.llm.group_log.events import KIND_EMOJI, KIND_MESSAGE, LogEvent
from app.llm.group_log.store import GroupLogStore

SCOPE = "group:1001"
NOW = int(time.time())


def _runtime(store: GroupLogStore | None, **config) -> SimpleNamespace:
    runtime = SimpleNamespace(bot_id=1, config=dict(config))
    if store is not None:
        runtime.group_log = store
    return runtime


def _store(**kw) -> GroupLogStore:
    return GroupLogStore(bot_id=1, retention_hours=0, **kw)


def _msg(mid: str, text: str, *, ts: int = NOW, user: str = "30003", nickname: str = "三哥"):
    return LogEvent(ts=ts, kind=KIND_MESSAGE, scope=SCOPE, group_id="1001", message_id=mid,
                    user_id=user, nickname=nickname, text=text)


def _emoji(mid: str, emoji_id: str = "66", *, by_me: bool = False):
    return LogEvent(ts=NOW, kind=KIND_EMOJI, scope=SCOPE, group_id="1001", message_id=mid,
                    user_id="30003", nickname="三哥", by_me=by_me,
                    payload={"emoji_id": emoji_id, "count": 1})


# ==================== 降级 ====================


def test_no_store_returns_empty():
    """模块未装/未挂载 → 空串，不抛异常（降级语义）。"""
    assert build_context_text(_runtime(None), "group_1001", group_id=1001) == ""


def test_disabled_config_returns_empty():
    store = _store()
    store.append_many([_msg("1", "你好")])
    runtime = _runtime(store, group_log_enable=False)
    assert build_context_text(runtime, "group_1001", group_id=1001) == ""


def test_empty_window_returns_empty():
    store = _store()
    runtime = _runtime(store, group_log_enable=True)
    assert build_context_text(runtime, "group_1001", group_id=1001) == ""


def test_broken_store_degrades_to_empty():
    class _Broken:
        def recent(self, *a, **kw):
            raise RuntimeError("爆了")

    runtime = _runtime(None, group_log_enable=True)
    runtime.group_log = _Broken()
    assert build_context_text(runtime, "group_1001", group_id=1001) == ""


def test_out_of_window_events_are_excluded():
    store = _store()
    store.append_many([_msg("old", "很久以前", ts=NOW - 7200), _msg("new", "刚刚", ts=NOW)])
    runtime = _runtime(store, group_log_enable=True, group_log_window_minutes=60)
    text = build_context_text(runtime, "group_1001", group_id=1001)
    assert "刚刚" in text
    assert "很久以前" not in text


# ==================== 内容与去重 ====================


def test_renders_environment_block_with_interactions():
    store = _store()
    store.append_many([_msg("1", "绝了"), _emoji("1")])
    runtime = _runtime(store, group_log_enable=True)
    text = build_context_text(runtime, "group_1001", group_id=1001)
    assert "绝了" in text
    assert "[♡66" in text
    assert text.startswith("【群聊环境记录")


def test_history_covered_message_is_deduped():
    """会话历史已覆盖的正文不再重复，但互动影子行保留。"""
    store = _store()
    store.append_many([_msg("1", "绝了"), _emoji("1"), _msg("2", "别的")])
    runtime = _runtime(store, group_log_enable=True)
    history = [{"role": "user", "content": "绝了", "message_id": "1"}]
    text = build_context_text(runtime, "group_1001", group_id=1001, history=history)
    assert "绝了" not in text
    assert "别的" in text
    assert "[♡66" in text


def test_my_actions_are_included():
    store = _store()
    store.append_many([_msg("1", "走吗"), _emoji("1", by_me=True)])
    runtime = _runtime(store, group_log_enable=True)
    text = build_context_text(runtime, "group_1001", group_id=1001)
    assert "我给这条贴了 66" in text


def test_private_scope_only_uses_private_bucket():
    """私聊只读私聊分片：群聊记录不能漏进私聊上下文。"""
    store = _store()
    store.append_many([_msg("1", "群里的话")])
    runtime = _runtime(store, group_log_enable=True)
    assert build_context_text(runtime, "private_30003", is_private=True, user_id=30003) == ""


def test_other_group_is_not_leaked():
    store = _store()
    store.append_many([_msg("1", "群一千的话")])
    runtime = _runtime(store, group_log_enable=True)
    assert build_context_text(runtime, "group_2002", group_id=2002) == ""


def test_window_limit_keeps_newest():
    store = _store()
    store.append_many([_msg(str(i), f"第{i}条", ts=NOW + i) for i in range(10)])
    runtime = _runtime(store, group_log_enable=True,
                       group_log_window_minutes=60, group_log_window_limit=3)
    text = build_context_text(runtime, "group_1001", group_id=1001)
    assert "第9条" in text
    assert "第0条" not in text


def test_char_budget_is_applied():
    store = _store()
    store.append_many([_msg(str(i), "很长的内容" * 40, ts=NOW + i) for i in range(20)])
    runtime = _runtime(store, group_log_enable=True, group_log_max_chars=400)
    text = build_context_text(runtime, "group_1001", group_id=1001)
    assert 0 < len(text) <= 400
