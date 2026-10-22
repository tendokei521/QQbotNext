"""配置分区契约：破坏性 v2 的「每个键都必须有归宿」硬性守卫。

## 守什么

v2 判定标准是「每个配置键只有三种命运：留在核心 / 删除 / 迁到模块」。
只要有一个键**没有归宿**，就意味着有人给框架加了一个
"还没想好归属、先塞进 agent 配置页"的开关 —— 那正是这次改造要根除的东西。

所以本文件断言：

1. `schema` 里每个键都在分区表里（`unknown_keys` 为空）；
2. 没有键被归到两处（`duplicate_keys` 为空，否则迁移会写两遍）；
3. 分区表里没有 schema 已不存在的键（`stale_keys` 为空，否则迁移脚本写空气）；
4. 三个归宿的键数之和 == schema 键数（分区是**划分**而非覆盖）；
5. 棘轮：schema 键数只减不增，直到降到 `TARGET_CORE_KEY_COUNT`。

## 为什么棘轮而不是直接断言 20

W0 阶段 schema 还是 160 键（迁移尚未执行），直接断言 20 会让全仓库变红。
棘轮的做法是：**当前值 160 作为起点，只允许下降**；每个迁移波次（W3-W7）
把 `BASELINE_SCHEMA_KEYS` 往下调一次，直到 20。这样"退化"（有人又加回一个键）会立刻失败。
"""

from __future__ import annotations

from app.agent.config_partition import (
    CORE_KEYS,
    DELETED_KEYS,
    MODULE_KEYS,
    TARGET_CORE_KEY_COUNT,
    all_partitioned_keys,
    duplicate_keys,
    fate_of,
    stale_keys,
    unknown_keys,
)
from app.agent.config_schema import SCHEMA

#: 当前 schema 的字段键（排除 group 声明与非 dict）
SCHEMA_KEYS: tuple[str, ...] = tuple(
    key
    for key, value in SCHEMA.items()
    if isinstance(value, dict) and value.get("type") != "group"
)

#: 棘轮起点（W7 完成后下调到 TARGET_CORE_KEY_COUNT=20）
BASELINE_SCHEMA_KEYS = 160


def test_every_schema_key_has_a_fate():
    """没有归宿的键 = 又一个"先塞进配置页"的技术债。"""
    orphans = unknown_keys(SCHEMA_KEYS)
    assert not orphans, (
        "这些键还没有归宿，请在 app/agent/config_partition.py 里归类"
        "（CORE_KEYS / DELETED_KEYS / MODULE_KEYS）：\n  "
        + "\n  ".join(orphans)
    )


def test_no_key_is_partitioned_twice():
    dups = duplicate_keys()
    assert not dups, f"这些键被归到多个归宿（迁移会写两遍）: {dups}"


def test_partition_has_no_stale_entries():
    """分区表里指向已不存在字段的键（迁移脚本会静默写空）。"""
    stale = stale_keys(SCHEMA_KEYS)
    assert not stale, f"分区表引用了 schema 里不存在的键: {stale}"


def test_partition_is_a_partition_not_an_overwrite():
    """三块之和必须等于 schema 键数 —— 保证没有键落在夹缝里。"""
    covered = len(all_partitioned_keys())
    assert covered == len(SCHEMA_KEYS), (
        f"分区覆盖 {covered} 键，schema 有 {len(SCHEMA_KEYS)} 键"
        f"（差值说明有键既不在分区表也不在 schema，或反之）"
    )


def test_schema_key_count_only_shrinks():
    """棘轮：迁移只许删减，不许有人再往 agent 配置页加键。"""
    assert len(SCHEMA_KEYS) <= BASELINE_SCHEMA_KEYS, (
        f"agent 配置键从 {BASELINE_SCHEMA_KEYS} 涨到 {len(SCHEMA_KEYS)}。"
        f"v2 口下新增行为请做成模块，不要加进 agent 配置。"
    )
    assert len(SCHEMA_KEYS) >= TARGET_CORE_KEY_COUNT, (
        f"键数 {len(SCHEMA_KEYS)} 少于目标核心 {TARGET_CORE_KEY_COUNT}，说明删过头了"
    )


def test_core_keys_all_still_exist():
    """核心键必须都真的存在于 schema（迁移波次误删核心键会立刻暴露）。"""
    missing = sorted(set(CORE_KEYS) - set(SCHEMA_KEYS))
    assert not missing, f"核心键在 schema 里消失了: {missing}"


def test_fate_lookup_matches_partition_shape():
    """`fate_of` 与三张表一致（防止脚本用错查询函数）。"""
    for key in CORE_KEYS:
        assert fate_of(key) == "core"
    for key in DELETED_KEYS:
        assert fate_of(key) == "deleted"
    for module_name, keys in MODULE_KEYS.items():
        for key in keys:
            assert fate_of(key) == f"module:{module_name}"


def test_module_plan_covers_every_migrated_key():
    """每个模块的目标键数之和 == 迁移总数（文档表格与实际表一致）。"""
    total = sum(len(keys) for keys in MODULE_KEYS.values())
    assert total == len(SCHEMA_KEYS) - len(CORE_KEYS) - len(DELETED_KEYS), (
        "迁移键数与「schema − 核心 − 删除」对不上"
    )


def test_planned_modules_are_declared():
    """13 个目标模块必须都在计划清单里（新增模块目录时同步此处）。"""
    planned = {
        "llm_output",
        "llm_reply_policy",
        "llm_context",
        "llm_compress",
        "llm_media",
        "llm_message_meta",
        "llm_memory",
        "llm_knowledge",
        "llm_tools_extra",
        "llm_proactive",
        "llm_schedule",
        "llm_assembly",
    }
    assert set(MODULE_KEYS) == planned, (
        f"模块清单漂移：多 {sorted(set(MODULE_KEYS) - planned)} / "
        f"少 {sorted(planned - set(MODULE_KEYS))}"
    )
