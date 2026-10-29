"""配置分区契约（v3 定稿）：本体 5 页 / 9 个模块 / 9 个删除。

## 现状（W7 完成后）

- `config_schema.SCHEMA` 已经是**收敛后的 43 键**（本体 5 页）；
- `config_schema.LEGACY_SCHEMA` 保留完整 187 键，供迁移脚本按旧键取类型/标签；
- 本文件同时校验"分区覆盖 187 键（无遗漏/无重复）"与"导出 SCHEMA 恰为 44 键"。
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
    TARGET_CORE_KEY_COUNT,
    UI_PAGES,
    all_partitioned_keys,
    duplicate_keys,
    fate_of,
    page_summary,
    panel_summary,
    reply_summary,
    stale_keys,
    ui_group_of,
    ui_page_of,
    unknown_keys,
)
from app.agent.config_schema import LEGACY_SCHEMA, SCHEMA

#: 迁移前的完整键集（185）
LEGACY_KEYS: tuple[str, ...] = tuple(
    key
    for key, value in LEGACY_SCHEMA.items()
    if isinstance(value, dict) and value.get("type") != "group"
)

#: 当前导出的键集（应为 43）
SCHEMA_KEYS: tuple[str, ...] = tuple(
    key
    for key, value in SCHEMA.items()
    if isinstance(value, dict) and value.get("type") != "group"
)

#: 本体配置页的顺序（拆分方案定稿：只剩对话行为 → 会话管理 → 高级功能）
EXPECTED_PAGE_ORDER = ("behavior", "session", "advanced")
#: 两个页面外工作台的字段数（AI 对话面板 / 回复与权限）
EXPECTED_PANEL_KEYS = 6
EXPECTED_REPLY_KEYS = 4

#: 9 个目标模块（目录名）
EXPECTED_MODULES = {
    "llm_output",
    "llm_context",
    "llm_message_meta",
    "llm_memory",
    "llm_knowledge",
    "llm_tools_extra",
    "llm_proactive",
    "llm_schedule",
    "llm_assembly",
}


# ==================== 收敛结果 ====================


def test_exported_schema_is_exactly_the_core_keys():
    """本体 schema 必须正好等于分区表里的本体键（多一个都是没搬干净）。"""
    assert set(SCHEMA_KEYS) == set(CORE_KEYS)
    assert len(SCHEMA_KEYS) == TARGET_CORE_KEY_COUNT == 57


def test_legacy_schema_still_has_all_legacy_keys():
    """旧 schema 必须完整保留（迁移脚本靠它取旧键的类型与标签）。"""
    assert len(LEGACY_KEYS) == 201


# ==================== 布局本身 ====================


def test_ui_has_exactly_three_pages_in_order():
    ordered = [p.id for p in sorted(UI_PAGES, key=lambda p: p.order)]
    assert tuple(ordered) == EXPECTED_PAGE_ORDER, (
        f"本体配置页页面顺序漂移：{ordered}，期望 {list(EXPECTED_PAGE_ORDER)}"
    )


def test_ui_page_field_counts_match_the_agreed_layout():
    counts = {row["id"]: row["fields"] for row in page_summary()}
    assert counts == {
        "behavior": 6,
        "session": 20,
        "advanced": 21,
    }, f"页面字段数漂移：{counts}"
    assert panel_summary()["fields"] == EXPECTED_PANEL_KEYS
    assert reply_summary()["fields"] == EXPECTED_REPLY_KEYS


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


# ==================== 数量与棘轮 ====================


def test_agreed_split_is_core_plus_module_plus_deleted():
    """数字：本体 57 / 迁移 109 / 删除 35（删掉 system_prompt，人格只由角色预设决定）。"""
    migrated = sum(len(keys) for keys in MODULE_KEYS.values())
    assert len(CORE_KEYS) == 57, f"本体键数 {len(CORE_KEYS)} ≠ 57"
    assert migrated == 109, f"迁移键数 {migrated} ≠ 109"
    assert len(DELETED_KEYS) == 35, f"删除键数 {len(DELETED_KEYS)} ≠ 35"
    assert len(CORE_KEYS) + migrated + len(DELETED_KEYS) == len(LEGACY_KEYS)


def test_module_inventory_is_nine():
    assert set(MODULE_KEYS) == EXPECTED_MODULES, (
        f"模块清单漂移：多 {sorted(set(MODULE_KEYS) - EXPECTED_MODULES)} / "
        f"少 {sorted(EXPECTED_MODULES - set(MODULE_KEYS))}"
    )


def test_schema_key_count_only_shrinks():
    """棘轮：schema 已收敛到 43，任何增长都是有人往 agent 配置页塞新键。"""
    assert len(SCHEMA_KEYS) <= TARGET_CORE_KEY_COUNT, (
        f"agent 配置键涨到 {len(SCHEMA_KEYS)}。v3 口径下新增行为请做成模块。"
    )


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
