"""配置布局契约（v3）：本体界面 5 页 / 9 个模块 / 9 个删除，每个键必须有归宿。

## 守在三条边界上

1. **v3 布局本身不漂移**：页面数量、顺序、每页字段集与分组，全部断言。
   前端 `sections.json` 将由此生成（W7），所以这里的断言就是前端行为的上游契约。
2. **没有键没有归宿**：任何新加的 agent 配置键，必须先在 `config_partition.py` 归类。
   这是"先塞进配置页、以后再想归属"的唯一堵口。
3. **只许收敛**：schema 键数棘轮只降不升，直到降到 `TARGET_CORE_KEY_COUNT`（43）。
"""

from __future__ import annotations

from app.agent.config_partition import (
    CORE_KEYS,
    DELETED_KEYS,
    MODULE_KEYS,
    OWNER_SOURCE_KEY,
    TARGET_CORE_KEY_COUNT,
    UI_PAGES,
    all_partitioned_keys,
    duplicate_keys,
    fate_of,
    page_summary,
    stale_keys,
    ui_group_of,
    ui_page_of,
    unknown_keys,
)
from app.agent.config_schema import SCHEMA

#: 当前 schema 的字段键（排除 group 声明与非 dict）
SCHEMA_KEYS: tuple[str, ...] = tuple(
    key
    for key, value in SCHEMA.items()
    if isinstance(value, dict) and value.get("type") != "group"
)

#: 棘轮起点（W7 完成后下调到 TARGET_CORE_KEY_COUNT）
BASELINE_SCHEMA_KEYS = 160

#: 本体界面的页面顺序（用户定稿：对话行为在会话管理之前）
EXPECTED_PAGE_ORDER = ("basic", "permission", "behavior", "session", "advanced")

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


# ==================== 布局本身 ====================


def test_ui_has_exactly_five_pages_in_order():
    ordered = [p.id for p in sorted(UI_PAGES, key=lambda p: p.order)]
    assert tuple(ordered) == EXPECTED_PAGE_ORDER, (
        f"本体界面页面顺序漂移：{ordered}，期望 {list(EXPECTED_PAGE_ORDER)}"
    )


def test_ui_page_field_counts_match_the_agreed_layout():
    """每页字段数是用户逐项确认过的，改动必须显式。"""
    counts = {row["id"]: row["fields"] for row in page_summary()}
    assert counts == {
        "basic": 7,
        "permission": 3,
        "behavior": 8,
        "session": 13,
        "advanced": 12,
    }, f"页面字段数漂移：{counts}"


def test_ui_groups_are_unique_and_named():
    group_ids = [g.id for page in UI_PAGES for g in page.groups]
    assert len(group_ids) == len(set(group_ids)), "分组 id 重复"
    assert all(g.label.strip() for page in UI_PAGES for g in page.groups), "分组缺 label"


def test_every_ui_key_belongs_to_exactly_one_group():
    for key in CORE_KEYS:
        assert ui_page_of(key) is not None, f"{key} 没有所属页面"
        assert ui_group_of(key) is not None, f"{key} 没有所属分组"
    dup = [k for k in set(CORE_KEYS) if CORE_KEYS.count(k) > 1]
    assert not dup, f"这些键在本体界面出现多次（会被渲染两遍）: {dup}"


def test_core_keys_are_derived_from_pages():
    """`CORE_KEYS` 必须完全由 `UI_PAGES` 派生（防止两处漂移）。"""
    assert CORE_KEYS == tuple(f for page in UI_PAGES for f in page.fields)


# ==================== 分区完整性 ====================


def test_every_schema_key_has_a_fate():
    orphans = unknown_keys(SCHEMA_KEYS)
    assert not orphans, (
        "这些键还没有归宿，请在 app/agent/config_partition.py 里归类"
        "（UI_PAGES / MODULE_KEYS / DELETED_KEYS）：\n  " + "\n  ".join(orphans)
    )


def test_no_key_is_partitioned_twice():
    dups = duplicate_keys()
    assert not dups, f"这些键被归到多个归宿（迁移会写两遍）: {dups}"


def test_partition_has_no_stale_entries():
    stale = stale_keys(SCHEMA_KEYS)
    assert not stale, f"布局表引用了 schema 里不存在的键: {stale}"


def test_partition_covers_every_schema_key_exactly_once():
    covered = len(all_partitioned_keys())
    assert covered == len(SCHEMA_KEYS), (
        f"分区覆盖 {covered} 键，schema 有 {len(SCHEMA_KEYS)} 键"
    )


# ==================== 数量与棘轮 ====================


def test_agreed_split_is_43_plus_108_plus_9():
    """用户定稿的数字：本体 43 / 迁移 108 / 删除 9 = 160。"""
    migrated = sum(len(keys) for keys in MODULE_KEYS.values())
    assert len(CORE_KEYS) == 43, f"本体键数 {len(CORE_KEYS)} ≠ 43"
    assert migrated == 108, f"迁移键数 {migrated} ≠ 108"
    assert len(DELETED_KEYS) == 9, f"删除键数 {len(DELETED_KEYS)} ≠ 9"
    assert len(CORE_KEYS) + migrated + len(DELETED_KEYS) == len(SCHEMA_KEYS)


def test_module_inventory_is_nine():
    assert set(MODULE_KEYS) == EXPECTED_MODULES, (
        f"模块清单漂移：多 {sorted(set(MODULE_KEYS) - EXPECTED_MODULES)} / "
        f"少 {sorted(EXPECTED_MODULES - set(MODULE_KEYS))}"
    )


def test_schema_key_count_only_shrinks():
    """棘轮：迁移只许删减，不许有人再往 agent 配置页加键。"""
    assert len(SCHEMA_KEYS) <= BASELINE_SCHEMA_KEYS, (
        f"agent 配置键从 {BASELINE_SCHEMA_KEYS} 涨到 {len(SCHEMA_KEYS)}。"
        f"v3 口径下新增行为请做成模块，不要加进 agent 配置。"
    )
    assert len(SCHEMA_KEYS) >= TARGET_CORE_KEY_COUNT, (
        f"键数 {len(SCHEMA_KEYS)} 少于目标本体键数 {TARGET_CORE_KEY_COUNT}，删过头了"
    )


def test_core_keys_all_still_exist():
    missing = sorted(set(CORE_KEYS) - set(SCHEMA_KEYS))
    assert not missing, f"本体界面的键在 schema 里消失了: {missing}"


# ==================== owner 身份单一来源 ====================


def test_owner_identity_comes_from_one_roster():
    """原账号级 owner_id 已并入 `agent_owner_list`：该键必须在权限页且存在。"""
    assert OWNER_SOURCE_KEY == "agent_owner_list"
    assert ui_page_of(OWNER_SOURCE_KEY) == "permission", "拥有者名册必须挂在权限管理页"
    assert OWNER_SOURCE_KEY in SCHEMA_KEYS, "拥有者名册必须存在于 schema"


def test_fate_lookup_is_consistent_with_tables():
    for key in DELETED_KEYS:
        assert fate_of(key) == "deleted"
    for page in UI_PAGES:
        for key in page.fields:
            assert fate_of(key) == f"ui:{page.id}"
    for module_name, keys in MODULE_KEYS.items():
        for key in keys:
            assert fate_of(key) == f"module:{module_name}"
