"""v3 迁移：账号级 owner_id → 拥有者名册（`scripts/migrate_v3_owner_to_roster.py`）。

这条迁移直接决定"升级后 owner 档模块还响不响应"，所以测试覆盖：
追加、去重、幂等、无 owner 跳过、账号未知跳过、手动模式、清空账号级字段。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "migrate_v3_owner_to_roster", ROOT / "scripts" / "migrate_v3_owner_to_roster.py"
)
assert _SPEC and _SPEC.loader
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)


def _bots(**owners):
    return {i: {"ws_url": f"ws://x/{i}", "owner_id": v} for i, v in owners.items()}


def test_plan_appends_owner_to_empty_roster():
    plan = migration.plan_migration(
        _bots(a=123),
        lambda account_id: {},
        lambda index, cfg: "10001",
    )
    assert len(plan) == 1
    assert plan[0]["before"] == []
    assert plan[0]["after"] == ["123"]
    assert plan[0]["changed"] is True


def test_plan_accepts_list_of_bots():
    """真实 `get_bots()` 返回的是 list，不是 dict。"""
    plan = migration.plan_migration(
        [{"ws_url": "ws://x", "owner_id": 123}],
        lambda account_id: {},
        lambda index, cfg: "10001",
    )
    assert plan and plan[0]["account_id"] == "10001"


def test_plan_keeps_existing_roster_and_appends():
    plan = migration.plan_migration(
        _bots(a=123),
        lambda account_id: {"agent_owner_list": ["999"]},
        lambda index, cfg: "10001",
    )
    assert plan[0]["after"] == ["999", "123"]


def test_plan_is_idempotent():
    """已在名册里的 owner 不重复追加（跑第二次必须无变化）。"""
    plan = migration.plan_migration(
        _bots(a=123),
        lambda account_id: {"agent_owner_list": ["123"]},
        lambda index, cfg: "10001",
    )
    assert plan[0]["after"] == ["123"]
    assert plan[0]["changed"] is False


def test_plan_skips_bots_without_owner():
    plan = migration.plan_migration(
        {0: {"ws_url": "ws://x"}, 1: {"ws_url": "ws://y", "owner_id": None}},
        lambda account_id: {},
        lambda index, cfg: "10001",
    )
    assert plan == []


def test_plan_skips_unknown_account():
    """账号还没登录过（拿不到 QQ）时跳过：宁可下次再迁，也不要写错账号。"""
    plan = migration.plan_migration(
        _bots(a=123),
        lambda account_id: {},
        lambda index, cfg: None,
    )
    assert plan == []


def test_plan_normalises_string_roster():
    plan = migration.plan_migration(
        _bots(a=123),
        lambda account_id: {"agent_owner_list": "999, 888 "},
        lambda index, cfg: "10001",
    )
    assert plan[0]["before"] == ["999", "888"]
    assert plan[0]["after"] == ["999", "888", "123"]


def test_manual_plan_works_without_account_snapshot():
    """快照缺失时的手动路径（`--bot-id` + `--owner-id`）。"""
    plan = migration.build_manual_plan("3437542570", "10001", lambda account_id: {})
    assert plan[0]["account_id"] == "3437542570"
    assert plan[0]["after"] == ["10001"]
    assert plan[0]["index"] is None


def test_manual_plan_requires_both_ids():
    with pytest.raises(ValueError):
        migration.build_manual_plan("", "10001", lambda account_id: {})
    with pytest.raises(ValueError):
        migration.build_manual_plan("3437542570", "", lambda account_id: {})


def test_apply_plan_writes_roster_and_clears_owner(capsys):
    stored: dict = {}
    cleared: list[int] = []

    def get_cfg(account_id):
        return dict(stored.get(account_id) or {})

    def set_cfg(account_id, cfg):
        stored[account_id] = dict(cfg)

    plan = migration.plan_migration(
        _bots(a=123),
        get_cfg,
        lambda index, cfg: "10001",
    )
    applied = migration.apply_plan(
        plan, get_cfg, set_cfg, cleared.append, dry_run=False
    )

    assert applied == 1
    assert stored["10001"]["agent_owner_list"] == ["123"]
    assert cleared == ["a"]


def test_apply_manual_plan_does_not_clear_any_bot():
    """手动模式没有 index，不该去动 bots 表。"""
    stored: dict = {}
    cleared: list[int] = []
    plan = migration.build_manual_plan("3437542570", "10001", lambda account_id: {})
    migration.apply_plan(
        plan,
        lambda account_id: dict(stored.get(account_id) or {}),
        lambda account_id, cfg: stored.setdefault(account_id, cfg),
        cleared.append,
        dry_run=False,
    )
    assert stored["3437542570"]["agent_owner_list"] == ["10001"]
    assert cleared == []


def test_apply_plan_dry_run_writes_nothing(capsys):
    stored: dict = {}
    cleared: list[int] = []
    plan = migration.plan_migration(
        _bots(a=123),
        lambda account_id: {},
        lambda index, cfg: "10001",
    )
    migration.apply_plan(
        plan,
        lambda account_id: dict(stored.get(account_id) or {}),
        lambda account_id, cfg: stored.setdefault(account_id, cfg),
        cleared.append,
        dry_run=True,
    )
    assert stored == {}
    assert cleared == []
    assert "[dry-run]" in capsys.readouterr().out


def test_explain_empty_says_why(capsys):
    """一条都没迁到时必须说清原因，不能静默"成功"。"""
    migration._explain_empty([{"ws_url": "ws://x", "owner_id": 123}], {})
    out = capsys.readouterr().out
    assert "[skip]" in out
    assert "--owner-id" in out  # 给出可执行的下一步

    migration._explain_empty([], {})
    assert "还没有任何账号" in capsys.readouterr().out

    migration._explain_empty([{"ws_url": "ws://x"}], {})
    assert "已经是 v3 形态" in capsys.readouterr().out
