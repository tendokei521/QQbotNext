"""四模块合并迁移的契约测试（不依赖真库）。

锁四件事：并集合并、启用位 OR、孤儿模块的值不静默丢、幂等（重跑不做事）。
"""

from __future__ import annotations

import pytest

from app.application.core_modules_merge import (
    MIGRATION_FLAG,
    MergeReport,
    migrate_four_modules,
)


class _FakeDB:
    def __init__(self, rows: dict[str, list[dict]]) -> None:
        self.rows = rows

    async def execute(self, sql: str, params=()) -> int:
        assert sql.strip().upper().startswith("DELETE")
        capability_id, scope_type, scope_id = params
        bucket = self.rows.get(capability_id, [])
        keep = [
            r
            for r in bucket
            if not (r["scope_type"] == scope_type and str(r.get("scope_id") or "") == str(scope_id or ""))
        ]
        removed = len(bucket) - len(keep)
        self.rows[capability_id] = keep
        return removed


class _FakeConfig:
    def __init__(self, rows: dict[str, list[dict]]) -> None:
        self.rows = rows
        self.kv: dict[str, object] = {}
        self.db = _FakeDB(rows)
        self.reloaded = 0

    async def get_kv(self, key, default=None):
        return self.kv.get(key, default)

    async def set_kv(self, key, value):
        self.kv[key] = value

    def list_bindings(self, capability_id):
        return [dict(r) for r in self.rows.get(capability_id, [])]

    def get_binding(self, capability_id, scope_type, scope_id=""):
        for row in self.rows.get(capability_id, []):
            if row["scope_type"] == scope_type and str(row.get("scope_id") or "") == str(scope_id or ""):
                return dict(row)
        return None

    async def save_binding(self, capability_id, scope_type, scope_id="", **fields):
        row = {
            "scope_type": scope_type,
            "scope_id": scope_id,
            "enabled": fields.get("enabled"),
            "config": fields.get("config"),
            "permission": fields.get("permission"),
        }
        self.rows.setdefault(capability_id, []).append(row)
        return row

    async def _load_bindings(self):
        self.reloaded += 1


def _row(enabled: bool, config: dict, scope_id: str = "123") -> dict:
    return {"scope_type": "account", "scope_id": scope_id, "enabled": enabled, "config": dict(config)}


@pytest.mark.asyncio
async def test_merges_union_and_enabled_or():
    cfg = _FakeConfig(
        {
            "llm_assembly": [_row(False, {"user_message_format": "single_line"})],
            "llm_message_meta": [_row(True, {"include_time": False})],
            "llm_proactive": [_row(False, {"proactive_friend_enable": True})],
            "llm_schedule": [_row(False, {"schedule_enable": False})],
        }
    )
    report = await migrate_four_modules(cfg)

    assert isinstance(report, MergeReport)
    assert report.migrated is True and not report.errors
    assert cfg.kv.get(MIGRATION_FLAG) == "1"

    prompt = cfg.get_binding("llm_prompt", "account", "123")
    assert prompt["config"] == {"user_message_format": "single_line", "include_time": False}
    assert prompt["enabled"] is True  # meta 是开的 → OR 为开

    trigger = cfg.get_binding("llm_trigger", "account", "123")
    assert trigger["config"] == {"proactive_friend_enable": True, "schedule_enable": False}
    assert trigger["enabled"] is False

    for old in ("llm_assembly", "llm_message_meta", "llm_proactive", "llm_schedule"):
        assert cfg.rows.get(old) == [], f"{old} 的旧行应该被删掉"
    assert cfg.reloaded == 1


@pytest.mark.asyncio
async def test_orphan_module_values_are_not_dropped_silently():
    cfg = _FakeConfig(
        {
            "llm_assembly": [_row(False, {})],
            "llm_knowledge": [_row(False, {"knowledge_enable": True, "knowledge_recall_limit": 3})],
        }
    )
    report = await migrate_four_modules(cfg)
    assert report.dropped == {
        "account:123": {"knowledge_enable": True, "knowledge_recall_limit": 3}
    }
    assert cfg.rows.get("llm_knowledge") == []


@pytest.mark.asyncio
async def test_idempotent_second_run_does_nothing():
    cfg = _FakeConfig({"llm_assembly": [_row(False, {"participation": "medium"})]})
    first = await migrate_four_modules(cfg)
    assert first.migrated is True

    second = await migrate_four_modules(cfg)
    assert second.migrated is False
    assert second.written == {}


@pytest.mark.asyncio
async def test_new_row_values_win_on_rerun():
    """新行已经存在（用户后来改过）时，旧值不许覆盖它。"""
    cfg = _FakeConfig({"llm_assembly": [_row(False, {"participation": "low"})]})
    cfg.rows["llm_prompt"] = [
        {"scope_type": "account", "scope_id": "123", "enabled": True, "config": {"participation": "high"}}
    ]
    await migrate_four_modules(cfg)
    assert cfg.get_binding("llm_prompt", "account", "123")["config"]["participation"] == "high"


@pytest.mark.asyncio
async def test_no_legacy_rows_does_not_set_flag():
    cfg = _FakeConfig({})
    report = await migrate_four_modules(cfg)
    assert report.migrated is False
    assert MIGRATION_FLAG not in cfg.kv
