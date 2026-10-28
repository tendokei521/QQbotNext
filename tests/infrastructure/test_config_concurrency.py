"""配置写路径并发保护：并发加号不丢账号、全量保存与绑定写入不互相抹掉。

历史问题（`account_store.save_bots` / `add_bot`）：

- `save_bots` 是「全量 upsert + 删除 payload 里没有的 id」，`add_bot` 是
  「读出全部 → append → 写回」；两个请求交错时后写者把前者的账号/改动覆盖掉；
- 多次 `await`（读缓存 → 拼 SQL → 事务 → 通知）之间没有串行化，跨请求可重入。

修法：`ConfigService` 一把按任务可重入的写锁覆盖全部写路径（读路径不取锁），
`add_bot` 改为锁内单行 INSERT（不再整表重写）。
"""

from __future__ import annotations

import asyncio


async def test_concurrent_add_bot_keeps_every_account(config_service, db):
    """10 个并发加号：id 全部唯一、一个都不能丢，库里与内存一致。"""
    ids = await asyncio.gather(
        *[config_service.add_bot({"ws_url": f"ws://bot{i}"}) for i in range(10)]
    )

    assert len(set(ids)) == 10, f"并发加号产生了重复 id: {ids}"
    assert sorted(b["id"] for b in config_service.get_bots()) == sorted(ids)

    rows = await db.fetchall("SELECT account_id FROM bots")
    assert {row["account_id"] for row in rows} == set(ids)


async def test_add_bot_does_not_rewrite_other_accounts(config_service, db):
    """加号只插自己这一行：先把已有账号的名字改了，再加号，不能被顺手写回旧值。"""
    await config_service.save_bots([{"id": "main", "name": "旧名", "ws_url": "ws://main"}])
    await config_service.add_bot({"id": "second", "name": "第二个", "ws_url": "ws://second"})

    rows = await db.fetchall("SELECT account_id, name FROM bots ORDER BY sort_order")
    assert [r["account_id"] for r in rows] == ["main", "second"]
    assert rows[0]["name"] == "旧名"


async def test_save_bots_and_binding_writes_interleave_without_loss(config_service, db):
    """全量保存账号与绑定/群覆盖/群列表写入交错：四类数据都要落库且内存一致。"""
    await config_service.save_bots([{"id": "a", "name": "甲", "ws_url": "ws://a"}])

    await asyncio.gather(
        config_service.save_bots([
            {"id": "a", "name": "甲", "ws_url": "ws://a"},
            {"id": "b", "name": "乙", "ws_url": "ws://b"},
        ]),
        config_service.save_binding("demo", "account", "a", enabled=True),
        config_service.upsert_known_groups("a", [
            {"group_id": 111, "group_name": "前端交流群", "member_count": 24},
        ]),
        config_service.save_channel_override("a", "agent", "111", enabled=True),
    )

    assert [b["id"] for b in config_service.get_bots()] == ["a", "b"]
    assert config_service.get_binding("demo", "account", "a")["enabled"] is True
    assert config_service.get_channel_override("a", "agent", "111")["enabled"] is True
    assert [g["group_id"] for g in config_service.list_known_groups("a")] == ["111"]

    bots_rows = await db.fetchall("SELECT account_id FROM bots")
    assert {r["account_id"] for r in bots_rows} == {"a", "b"}
    binding = await db.fetchone(
        "SELECT enabled FROM bindings WHERE capability_id = ? AND scope_type = ? AND scope_id = ?",
        ("demo", "account", "a"),
    )
    assert binding and binding["enabled"] == 1
    override = await db.fetchone(
        "SELECT enabled FROM channel_overrides WHERE account_id = ? AND capability = ? AND channel_id = ?",
        ("a", "agent", "111"),
    )
    assert override and override["enabled"] == 1
    group = await db.fetchone(
        "SELECT name FROM known_groups WHERE account_id = ? AND group_id = ?", ("a", "111")
    )
    assert group and group["name"] == "前端交流群"


async def test_concurrent_full_saves_do_not_mix_payloads(config_service, db):
    """两个全量保存并发：最终状态必须是其中一份完整 payload，不能是交错的半套。"""
    await asyncio.gather(
        config_service.save_bots([{"id": "a", "name": "甲", "ws_url": "ws://a"}]),
        config_service.save_bots([{"id": "b", "name": "乙", "ws_url": "ws://b"}]),
    )

    memory_ids = [b["id"] for b in config_service.get_bots()]
    rows = await db.fetchall("SELECT account_id FROM bots ORDER BY sort_order")
    db_ids = [row["account_id"] for row in rows]

    assert memory_ids in (["a"], ["b"]), f"内存出现了交错的半套配置: {memory_ids}"
    assert db_ids == memory_ids, f"库与内存不一致: db={db_ids} memory={memory_ids}"


async def test_write_lock_is_reentrant_for_nested_writes(config_service):
    """删除账号会嵌套调用全量保存（delete_bot → save_bots），重入锁不能自锁死。"""
    await config_service.add_bot({"id": "gone", "name": "要删的", "ws_url": "ws://gone"})

    assert await asyncio.wait_for(config_service.delete_bot("gone"), timeout=5) is True
    assert config_service.get_bots() == []
