"""``app/llm/group_log`` 记录面测试：事件键、幂等、保留、持久化、分片隔离。"""

from __future__ import annotations

import time
from types import SimpleNamespace

from app.llm.group_log.events import (
    KIND_EMOJI,
    KIND_MESSAGE,
    KIND_POKE,
    LogEvent,
    make_scope,
)
from app.llm.group_log.store import GroupLogStore, attach, detach, store_of

GROUP = make_scope(True, group_id=1001)


def _msg(text: str = "你好", *, mid: str = "1", ts: int | None = None, **kw) -> LogEvent:
    opts = {"kind": KIND_MESSAGE, "scope": GROUP, "group_id": "1001",
            "user_id": "30003", "nickname": "三哥"}
    opts.update(kw)
    return LogEvent(
        ts=ts if ts is not None else int(time.time()),
        message_id=mid,
        text=text,
        **opts,
    )


def _emoji(emoji_id: str, *, mid: str = "1", user: str = "30003", is_add: bool = True, **kw) -> LogEvent:
    opts = {"ts": int(time.time()), "scope": GROUP, "group_id": "1001", "by_me": False}
    opts.update(kw)
    return LogEvent(
        kind=KIND_EMOJI,
        message_id=mid,
        user_id=user,
        payload={"emoji_id": emoji_id, "is_add": is_add},
        **opts,
    )


# ==================== 事件键 ====================


def test_event_key_dedupes_same_message_and_keeps_emoji_actor():
    first = _msg("你好", mid="1")
    assert first.key == _msg("内容变了但 id 相同", mid="1").key  # 同一条消息只记一条

    one = _emoji("66", user="1")
    assert one.key != _emoji("66", user="2").key  # 不同人贴同一表情是两条
    assert one.key != _emoji("66", user="1", is_add=False).key  # 贴 vs 取消可区分


def test_event_key_poke_uses_five_second_bucket():
    def poke(ts: int):
        return LogEvent(ts=ts, kind=KIND_POKE, scope=GROUP, user_id="1",
                        payload={"target_id": "2"})

    assert poke(1000).key == poke(1002).key
    assert poke(1000).key != poke(1006).key


def test_event_key_without_message_id_is_deterministic():
    a = _msg("无 id", mid="", ts=1700000000)
    b = _msg("无 id", mid="", ts=1700000000)
    assert a.key == b.key


# ==================== 幂等与保留 ====================


def test_append_many_is_idempotent():
    store = GroupLogStore(bot_id=1, retention_hours=0)
    assert store.append_many([_msg(mid="1"), _msg(mid="1")]) == 1
    assert store.stats["deduped"] == 1
    assert len(store.recent(GROUP)) == 1


def test_capacity_eviction_clears_index_so_event_can_be_reappended():
    store = GroupLogStore(bot_id=1, retention_count=2, retention_hours=0)
    store.append_many([_msg(mid="1"), _msg(mid="2")])
    store.append_many([_msg(mid="3")])

    assert [e.message_id for e in store.recent(GROUP)] == ["2", "3"]
    assert store.stats["dropped_by_retention"] == 1
    # 被淘汰的旧事件不在幂等索引里 → 可以重新入账（否则日志会永久卡死）
    assert store.append_many([_msg(mid="1")]) == 1


def test_prune_by_hours_uses_ingest_stamps():
    """过了保留时长的记录会被淘汰（保留窗口按入账时间滚动）。"""
    store = GroupLogStore(bot_id=1, retention_hours=1)
    store.append_many([_msg(mid="old"), _msg(mid="new")])
    assert len(store.recent(GROUP)) == 2
    # 把两条的入账时间伪造成 2 小时前 → 均已过期
    store._stamps[GROUP] = type(store._stamps[GROUP])([time.time() - 7200] * 2)
    assert store.prune(GROUP) == 2
    assert store.recent(GROUP) == []
    assert store.stats["dropped_by_retention"] == 2


def test_zero_retention_count_rejects_everything():
    store = GroupLogStore(bot_id=1, retention_count=0)
    assert store.append_many([_msg()]) == 0
    assert store.recent(GROUP) == []
    assert store.stats["dropped_by_retention"] == 1


def test_retention_uses_ingest_time_not_event_time():
    """迟到/补录的旧消息不能被"按事件时间"立刻淘汰掉（曾因此整类记录静默消失）。"""
    store = GroupLogStore(bot_id=1, retention_hours=1)
    store.append_many([_msg(mid="late", ts=int(time.time()) - 86400 * 3)])
    assert [e.message_id for e in store.recent(GROUP)] == ["late"]


# ==================== 窗口与分片隔离 ====================


def test_recent_window_and_limit():
    now = int(time.time())
    store = GroupLogStore(bot_id=1, retention_hours=0)
    # 入账顺序即返回顺序（mid=0 最早入账、mid=4 最晚）；limit 保留**最新的**尾部
    store.append_many([_msg(mid=str(i), ts=now - i) for i in range(5)])
    assert [e.message_id for e in store.recent(GROUP, minutes=1)] == ["0", "1", "2", "3", "4"]
    assert [e.message_id for e in store.recent(GROUP, limit=2)] == ["3", "4"]
    assert [e.message_id for e in store.recent(GROUP, minutes=1, limit=3)] == ["2", "3", "4"]


def test_recent_filters_events_outside_window():
    now = int(time.time())
    store = GroupLogStore(bot_id=1, retention_hours=0)
    store.append_many([_msg(mid="old", ts=now - 3600), _msg(mid="new", ts=now)])
    assert [e.message_id for e in store.recent(GROUP, minutes=10)] == ["new"]


def test_scopes_are_isolated():
    other = make_scope(True, group_id=2002)
    private = make_scope(False, user_id="30003")
    store = GroupLogStore(bot_id=1, retention_hours=0)
    store.append_many([
        _msg(mid="1"),
        _msg(mid="9", scope=other, group_id="2002"),
        LogEvent(ts=int(time.time()), kind=KIND_POKE, scope=private,
                 user_id="30003", payload={"target_id": "1"}),
    ])
    assert [e.message_id for e in store.recent(GROUP)] == ["1"]
    assert [e.message_id for e in store.recent(other)] == ["9"]
    assert store.recent(GROUP) != store.recent(other)
    assert len(store.recent(private)) == 1


# ==================== 表情聚合与"我的动作" ====================


def test_reactions_of_aggregates_add_and_remove():
    store = GroupLogStore(bot_id=1, retention_hours=0)
    store.append_many([
        _emoji("66", user="1"),
        _emoji("66", user="2"),
        _emoji("77", user="1"),
    ])
    reactions = {r["emoji_id"]: r for r in store.reactions_of(GROUP, "1")}
    assert reactions["66"]["count"] == 2
    assert reactions["66"]["users"] == ["1", "2"]
    assert reactions["77"]["count"] == 1

    # 取消后计数回落；归零的表情不再出现（幂等判断要用它）
    store.append_many([_emoji("66", user="2", is_add=False), _emoji("77", user="1", is_add=False)])
    reactions = {r["emoji_id"]: r for r in store.reactions_of(GROUP, "1")}
    assert reactions["66"]["count"] == 1
    assert "77" not in reactions


def test_my_actions_on_separates_me_from_others():
    """同一人贴同一表情时，"我贴的"和"别人贴的"要分开记（否则机器人动作查不到）。"""
    store = GroupLogStore(bot_id=1, retention_hours=0)
    store.append_many([
        _emoji("66", user="1"),
        _emoji("66", user="1", by_me=True),
        _emoji("77", user="1", by_me=True),
    ])
    assert store.my_actions_on(GROUP, "1") == ["emoji:66", "emoji:77"]
    # 两条都记进聚合：别人一个 + 我一个
    assert store.reactions_of(GROUP, "1")[0]["count"] == 2


def test_mark_conversation_covered_is_record_only():
    store = GroupLogStore(bot_id=1, retention_hours=0)
    store.append_many([_msg(mid="1")])
    store.mark_conversation_covered(GROUP, ["1", "", None])
    assert store.covered_ids(GROUP) == {"1"}
    assert len(store.recent(GROUP)) == 1  # 存储不受影响


# ==================== 持久化 ====================


async def test_flush_and_restore_across_restart(tmp_path):
    store = GroupLogStore(bot_id=7, data_dir=tmp_path, retention_hours=24)
    store.append_many([_msg(mid="1"), _emoji("66")])
    assert await store.flush() == 2
    await store.close()

    revived = GroupLogStore(bot_id=7, data_dir=tmp_path, retention_hours=24)
    await revived.start()
    try:
        assert [e.message_id for e in revived.recent(GROUP)] == ["1", "1"]
        assert revived.reactions_of(GROUP, "1")[0]["emoji_id"] == "66"
        assert revived.stats["loaded"] == 2
    finally:
        await revived.close()


async def test_bad_line_does_not_break_restore(tmp_path):
    directory = tmp_path / "7" / "group_1001"
    directory.mkdir(parents=True)
    (directory / f"{time.strftime('%Y-%m-%d')}.jsonl").write_text(
        '{"ts": 1, "kind": "message", "scope": "group:1001", "message_id": "1"}\n'
        "这不是 JSON\n"
        '{"ts": 2, "kind": "message", "scope": "group:1001", "message_id": "2"}\n',
        encoding="utf-8",
    )
    store = GroupLogStore(bot_id=7, data_dir=tmp_path, retention_hours=0)
    await store.start()
    try:
        assert [e.message_id for e in store.recent(GROUP)] == ["1", "2"]
    finally:
        await store.close()


async def test_write_loop_persists_without_manual_flush(tmp_path):
    store = GroupLogStore(bot_id=7, data_dir=tmp_path, retention_hours=24, flush_interval=0.05)
    await store.start()
    try:
        store.append_many([_msg(mid="42")])
        for _ in range(20):
            await __import__("asyncio").sleep(0.02)
            if store.stats["written"]:
                break
        assert store.stats["written"] >= 1
    finally:
        await store.close()


# ==================== 运行时挂载点 ====================


def test_attach_detach_and_store_of():
    runtime = SimpleNamespace()
    assert store_of(runtime) is None  # 未挂载即降级，不抛
    store = GroupLogStore(bot_id=1, retention_hours=0)
    attach(runtime, store)
    assert store_of(runtime) is store
    detach(runtime)
    assert store_of(runtime) is None
    runtime.group_log = "不是 store"
    assert store_of(runtime) is None  # 类型不对也按未挂载处理
