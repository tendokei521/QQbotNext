"""v3 配置拆分迁移测试（`scripts/migrate_v3_split_config.py`）。

这条迁移决定"升级后用户的记忆/主动消息/流式等设置还在不在"，所以测试要覆盖：
留本体、搬到模块、模块已有值优先、丢弃记录、未知键保留、幂等。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "migrate_v3_split_config", ROOT / "scripts" / "migrate_v3_split_config.py"
)
assert _SPEC and _SPEC.loader
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)

from app.agent.config_partition import CORE_KEYS, DELETED_KEYS, MODULE_KEYS  # noqa: E402

#: 所有模块键（跟着分区表走，加键不用改测试）
MODULE_KEY_SET = {k for keys in MODULE_KEYS.values() for k in keys}


def _full_legacy_config() -> dict:
    """造一份"160 键全填"的旧配置（每个键给一个可辨识的值）。"""
    cfg: dict = {}
    for key in CORE_KEYS:
        cfg[key] = f"core:{key}"
    for key in DELETED_KEYS:
        cfg[key] = f"dead:{key}"
    for keys in MODULE_KEYS.values():
        for key in keys:
            cfg[key] = f"mod:{key}"
    return cfg


# ---------- 纯函数 ----------


def test_plan_keeps_core_keys_in_agent_row():
    plan = migration.plan_split(_full_legacy_config())
    assert set(plan["agent_remaining"]) == set(CORE_KEYS)
    assert len(plan["agent_remaining"]) == len(CORE_KEYS) == 55


def test_plan_moves_all_module_keys_to_the_right_module():
    plan = migration.plan_split(_full_legacy_config())
    total = sum(len(v) for v in plan["modules"].values())
    assert total == len(MODULE_KEY_SET)
    for module_name, moved in plan["modules"].items():
        assert set(moved) == set(MODULE_KEYS[module_name]), module_name
        for key in moved:
            assert moved[key] == f"mod:{key}"


def test_plan_records_deleted_keys():
    plan = migration.plan_split(_full_legacy_config())
    assert set(plan["dropped"]) == set(DELETED_KEYS)
    assert len(plan["dropped"]) == len(DELETED_KEYS) == 34


def test_plan_keeps_unknown_keys_in_agent_row():
    """第三方插件写进 agent 行的键不能被顺手删掉。"""
    plan = migration.plan_split({"group_enable": True, "some_plugin_key": 42})
    assert plan["agent_remaining"]["some_plugin_key"] == 42
    assert plan["unknown"] == ["some_plugin_key"]


def test_plan_is_idempotent_on_already_migrated_config():
    """已经迁过的 agent 行（只剩本体键）再跑一次：什么都不该搬。"""
    already = {key: "v" for key in CORE_KEYS}
    plan = migration.plan_split(already)
    assert plan["modules"] == {}
    assert plan["dropped"] == {}
    assert set(plan["agent_remaining"]) == set(CORE_KEYS)


def test_plan_handles_empty_config():
    plan = migration.plan_split({})
    assert plan == {
        "agent_remaining": {},
        "modules": {},
        "dropped": {},
        "unknown": [],
    }


# ---------- 落库 ----------


class _FakeService:
    def __init__(self, initial: dict | None = None):
        self.data: dict[tuple[str, str], dict] = initial or {}

    def get_module_config(self, module, bot_id):
        return dict(self.data.get((module, str(bot_id))) or {})

    async def save_module_config(self, module, bot_id, config):
        self.data[(module, str(bot_id))] = dict(config)


async def test_apply_split_writes_modules_and_trims_agent_row():
    service = _FakeService()
    plan = migration.plan_split(_full_legacy_config())
    dropped: dict = {}

    stats = await migration.apply_split(
        service, "12345", plan, dry_run=False, dropped_sink=dropped
    )

    assert stats["agent_keys"] == len(CORE_KEYS) == 55
    assert sum(stats["modules"].values()) == len(MODULE_KEY_SET)
    # agent 行只剩本体键
    assert set(service.get_module_config("agent", "12345")) == set(CORE_KEYS)
    # 每个模块行都拿到了自己的键
    for module_name, keys in MODULE_KEYS.items():
        stored = service.get_module_config(module_name, "12345")
        assert set(stored) == set(keys), module_name
    # 丢弃的键被记录下来
    assert set(dropped["12345"]) == set(DELETED_KEYS)


async def test_apply_split_never_overwrites_existing_module_values():
    """模块页里用户新配的值优先于 agent 行的旧值。"""
    service = _FakeService({("llm_memory", "12345"): {"memory_enable": True}})
    plan = migration.plan_split({"memory_enable": False, "group_enable": True})

    await migration.apply_split(service, "12345", plan, dry_run=False)

    assert service.get_module_config("llm_memory", "12345")["memory_enable"] is True


async def test_apply_split_dry_run_writes_nothing():
    service = _FakeService()
    plan = migration.plan_split(_full_legacy_config())

    stats = await migration.apply_split(service, "1", plan, dry_run=True)

    assert service.data == {}
    assert sum(stats["modules"].values()) == len(MODULE_KEY_SET)


async def test_apply_split_is_idempotent():
    service = _FakeService()
    plan = migration.plan_split(_full_legacy_config())
    await migration.apply_split(service, "1", plan, dry_run=False)
    snapshot = {k: dict(v) for k, v in service.data.items()}

    again = migration.plan_split(service.get_module_config("agent", "1"))
    await migration.apply_split(service, "1", again, dry_run=False)

    assert service.data == snapshot, "第二次迁移不该改变任何内容"


def test_dump_dropped_writes_json(tmp_path):
    target = tmp_path / "dropped.json"
    migration._dump_dropped(target, {"1": {"max_tool_rounds": 5}})
    import json

    assert json.loads(target.read_text(encoding="utf-8")) == {"1": {"max_tool_rounds": 5}}


def test_dump_dropped_skips_empty(tmp_path):
    target = tmp_path / "none.json"
    migration._dump_dropped(target, {})
    assert not target.exists()


@pytest.mark.parametrize("module_name", sorted(MODULE_KEYS))
def test_every_module_gets_only_its_own_keys(module_name):
    plan = migration.plan_split(_full_legacy_config())
    assert set(plan["modules"][module_name]) == set(MODULE_KEYS[module_name])
