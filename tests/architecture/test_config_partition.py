"""配置分区契约：本体界面 / 模块 / 删除项的归属必须自洽。

## 测什么（只保留规则，不锁数量）

- 导出给本体的 schema 必须**恰好**等于分区表里的本体键（多一个都是没搬干净）；
- 旧键必须全部有归宿（无遗漏、无重复、无引用不存在的键）；
- 每个本体键有且只有一个所属页面/分组；
- 拥有者名册只能来自单一来源。

键数/页面字段数/模块清单等「当前布局快照」已按 `docs/TEST_POLICY.md §5` 删除：
它们在每次调整配置时都会红，却不提供回归价值。
"""

from __future__ import annotations

from app.agent.config_partition import (
    CORE_KEYS,
    DELETED_KEYS,
    MODULE_KEYS,
    OWNER_SOURCE_KEY,
    PANEL_KEYS,
    REPLY_KEYS,
    SURFACE_GROUPS,
    UI_PAGES,
    all_partitioned_keys,
    duplicate_keys,
    fate_of,
    stale_keys,
    ui_group_of,
    ui_page_of,
    unknown_keys,
)
from app.agent.config_schema import LEGACY_SCHEMA, SCHEMA

#: 迁移前的完整键集
LEGACY_KEYS: tuple[str, ...] = tuple(
    key
    for key, value in LEGACY_SCHEMA.items()
    if isinstance(value, dict) and value.get("type") != "group"
)

#: 当前导出的键集
SCHEMA_KEYS: tuple[str, ...] = tuple(
    key
    for key, value in SCHEMA.items()
    if isinstance(value, dict) and value.get("type") != "group"
)


# ==================== 收敛结果 ====================


def test_exported_schema_is_exactly_the_core_keys():
    """本体 schema 必须正好等于分区表里的本体键（多一个都是没搬干净）。"""
    assert set(SCHEMA_KEYS) == set(CORE_KEYS)


# ==================== 布局自洽 ====================


def test_ui_groups_are_unique_and_named():
    all_groups = [g for page in UI_PAGES for g in page.groups]
    for groups in SURFACE_GROUPS.values():
        all_groups.extend(groups)
    group_ids = [g.id for g in all_groups]
    assert len(group_ids) == len(set(group_ids)), "分组 id 重复"
    assert all(g.label.strip() for g in all_groups), "分组缺 label"


def test_every_ui_key_belongs_to_exactly_one_group():
    for key in CORE_KEYS:
        assert ui_page_of(key) is not None, f"{key} 没有所属页面"
        assert ui_group_of(key) is not None, f"{key} 没有所属分组"
    dup = [k for k in set(CORE_KEYS) if CORE_KEYS.count(k) > 1]
    assert not dup, f"这些键在本体界面出现多次（会被渲染两遍）: {dup}"


def test_core_keys_are_derived_from_pages_and_surfaces():
    expected = (
        PANEL_KEYS
        + tuple(f for page in UI_PAGES for f in page.fields)
        + REPLY_KEYS
    )
    assert CORE_KEYS == expected


# ==================== 分区完整性（对旧 160 键）====================


def test_every_legacy_key_has_a_fate():
    orphans = unknown_keys(LEGACY_KEYS)
    assert not orphans, (
        "这些键还没有归宿，请在 app/agent/config_partition.py 里归类"
        "（UI_PAGES / MODULE_KEYS / DELETED_KEYS）：\n  " + "\n  ".join(orphans)
    )


def test_no_key_is_partitioned_twice():
    dups = duplicate_keys()
    assert not dups, f"这些键被归到多个归宿（迁移会写两遍）: {dups}"


def test_partition_has_no_stale_entries():
    stale = stale_keys(LEGACY_KEYS)
    assert not stale, f"布局表引用了旧 schema 里不存在的键: {stale}"


def test_partition_covers_every_legacy_key_exactly_once():
    covered = len(all_partitioned_keys())
    assert covered == len(LEGACY_KEYS), (
        f"分区覆盖 {covered} 键，旧 schema 有 {len(LEGACY_KEYS)} 键"
    )


# ==================== 键集不变量 ====================


def test_core_keys_all_still_exist():
    missing = sorted(set(CORE_KEYS) - set(SCHEMA_KEYS))
    assert not missing, f"本体界面的键在 schema 里消失了: {missing}"


# ==================== owner 身份单一来源 ====================


def test_owner_identity_comes_from_one_roster():
    assert OWNER_SOURCE_KEY == "agent_owner_list"
    assert ui_page_of(OWNER_SOURCE_KEY) == "reply", "拥有者名册必须挂在「回复与权限」工作台"
    assert OWNER_SOURCE_KEY in SCHEMA_KEYS, "拥有者名册必须存在于 schema"


def test_fate_lookup_is_consistent_with_tables():
    for key in DELETED_KEYS:
        assert fate_of(key) == "deleted"
    for page in UI_PAGES:
        for key in page.fields:
            assert fate_of(key) == f"ui:{page.id}"
    for surface_id, groups in SURFACE_GROUPS.items():
        for group in groups:
            for key in group.fields:
                assert fate_of(key) == f"ui:{surface_id}"
    for module_name, keys in MODULE_KEYS.items():
        for key in keys:
            assert fate_of(key) == f"module:{module_name}"
