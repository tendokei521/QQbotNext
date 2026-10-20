"""Agent / 高级设置分区 registry 与后端配置 schema 的跨语言契约测试。

前端 ``dashboard/src/agent/sections.json`` 是「哪个字段出现在哪一页的哪个分组」的唯一权威：
路由、侧边栏二级菜单、段内导航、概览卡片都由它派生。本文件把这层关系钉住，
防止出现「配置项在 UI 里没有入口」「同一个配置项被两页重复渲染」这类漂移。

v3 起 registry 自己拥有**页内子分组**（``sections[].groups[]``）：后端 group 是模块划分
（group_session / group_tools / group_proactive …），用户心智是任务划分，一个页面常常
横跨多个后端 group，所以页内分组用 ``fields``（显式清单）或 ``source``（收编整个后端
group）来声明，后端不需要知道前端怎么排版。

另有 ``retired``：被并行计划接管、前端不再提供入口的字段。它们是**合法存在**的后端字段，
只是不该出现在任何页面里。
"""

from __future__ import annotations

import json
from pathlib import Path

from app.agent.config_schema import SCHEMA

SECTIONS_PATH = (
    Path(__file__).resolve().parents[1] / "dashboard" / "src" / "agent" / "sections.json"
)

# 单个分区承载的字段上限：超过就该再拆一页。
# 历史上「基础配置」一页塞了 42 个字段，是这次重划的直接动因。
MAX_FIELDS_PER_SECTION = 30

#: 允许出现在「基础配置」里的 advanced 字段（各自的理由写在注释里）。
#: 除此之外基础配置必须全是 basic 档，否则新手必经页会被调参项淹没。
ADVANCED_ALLOWED_IN_BASIC = {
    # 行为规则的手写覆盖：默认规则不合心意时才会用到，但和「行为规则开关」
    # 必须相邻出现才有意义，单独挪进进阶区反而更难找。
    "behavior_rules_override",
}

#: 这些后端分组必须体现字段级 importance 分层（组内既有 basic 又有 advanced）
_LAYERED_GROUPS = {
    "group_session",
    "group_stream",
    "group_proactive",
    "group_memory",
    "group_tools",
}

_AREAS = {"agent", "advanced"}


# ==================== 读取与派生 ====================


def _load_manifest() -> dict:
    return json.loads(SECTIONS_PATH.read_text(encoding="utf-8"))


def _load_sections() -> list[dict]:
    return _load_manifest()["sections"]


def _retired_fields() -> set[str]:
    return set((_load_manifest().get("retired") or {}).get("fields") or [])


def _schema_items() -> dict[str, dict]:
    """schema 里的配置项（排除 group 定义）。"""
    return {
        key: definition
        for key, definition in SCHEMA.items()
        if isinstance(definition, dict) and definition.get("type") != "group"
    }


def _schema_group_ids() -> set[str]:
    return {
        key
        for key, definition in SCHEMA.items()
        if isinstance(definition, dict) and definition.get("type") == "group"
    }


def _subgroup_fields(subgroup: dict) -> list[str]:
    """复刻前端 buildSectionSchema：显式 fields 优先，否则按 source 收整组。"""
    items = _schema_items()
    if subgroup.get("fields"):
        return [key for key in subgroup["fields"] if key in items]
    sources = set(subgroup.get("source") or [])
    excluded = set(subgroup.get("excludeFields") or [])
    return [
        key
        for key, definition in items.items()
        if definition.get("group") in sources and key not in excluded
    ]


def _fields_by_section() -> dict[str, list[str]]:
    return {
        section["id"]: [
            key
            for subgroup in section.get("groups") or []
            for key in _subgroup_fields(subgroup)
        ]
        for section in _load_sections()
    }


def _all_subgroups() -> list[tuple[dict, dict]]:
    return [
        (section, subgroup)
        for section in _load_sections()
        for subgroup in section.get("groups") or []
    ]


# ==================== 清单结构 ====================


def test_sections_manifest_exists_and_parses():
    assert SECTIONS_PATH.is_file(), f"缺少分区清单: {SECTIONS_PATH}"
    assert _load_sections(), "分区清单不能为空"


def test_section_ids_and_paths_are_unique():
    sections = _load_sections()
    ids = [s["id"] for s in sections]
    paths = [s["path"] for s in sections]
    assert len(ids) == len(set(ids)), f"分区 id 重复: {ids}"
    assert len(paths) == len(set(paths)), f"分区路径重复: {paths}"


def test_every_section_has_required_metadata():
    for section in _load_sections():
        for key in ("id", "path", "title", "subtitle", "icon", "color", "area", "order", "groups"):
            assert section.get(key) not in (None, ""), f"分区 {section.get('id')} 缺字段 {key}"
        assert section["path"].startswith("/"), f"分区 {section['id']} 路径应以 / 开头"
        assert isinstance(section["groups"], list) and section["groups"], (
            f"分区 {section['id']} 必须声明至少一个页内分组"
        )


def test_section_area_matches_path():
    """归属区域决定路由挂在哪：agent → /agent/*，advanced → /advanced/*。"""
    for section in _load_sections():
        area = section["area"]
        assert area in _AREAS, f"分区 {section['id']} 的 area 非法: {area}"
        assert section["path"].startswith(f"/{area}/"), (
            f"分区 {section['id']} 归属 {area} 区域，路径却不在 /{area}/ 下"
        )


def test_every_subgroup_is_well_formed():
    """页内分组必须有 id/label，且至少声明一种字段来源。"""
    seen: dict[str, str] = {}
    for section, subgroup in _all_subgroups():
        sid = subgroup.get("id")
        assert sid, f"分区 {section['id']} 里有页内分组缺 id"
        assert subgroup.get("label"), f"页内分组 {sid} 缺 label（段内导航会显示空标签）"
        assert subgroup.get("fields") or subgroup.get("source"), (
            f"页内分组 {sid} 既没有 fields 也没有 source，渲染出来是空卡片"
        )
        assert sid not in seen, f"页内分组 id 重复: {sid}（{seen.get(sid)} 与 {section['id']}）"
        seen[sid] = section["id"]


def test_subgroups_only_reference_existing_fields():
    items = set(_schema_items())
    for section, subgroup in _all_subgroups():
        for key in subgroup.get("fields") or []:
            assert key in items, f"{section['id']}/{subgroup['id']} 引用了不存在的字段: {key}"


def test_subgroups_only_reference_existing_groups():
    groups = _schema_group_ids()
    for section, subgroup in _all_subgroups():
        for group in subgroup.get("source") or []:
            assert group in groups, f"{section['id']}/{subgroup['id']} 引用了不存在的 group: {group}"


# ==================== 覆盖契约（核心） ====================


def test_every_field_is_covered_exactly_once():
    """核心契约：每个字段要么恰好被一个页内分组承载，要么已被退役。"""
    retired = _retired_fields()
    owners: dict[str, list[str]] = {}
    for section, subgroup in _all_subgroups():
        for key in _subgroup_fields(subgroup):
            owners.setdefault(key, []).append(f"{section['id']}/{subgroup['id']}")

    unmapped = [key for key in _schema_items() if key not in owners and key not in retired]
    duplicated = {key: where for key, where in owners.items() if len(where) > 1}
    resurrected = sorted(set(owners) & retired)

    assert not unmapped, f"以下字段没有任何入口（UI 里将无法配置）: {unmapped}"
    assert not duplicated, f"以下字段被多个页内分组重复承载: {duplicated}"
    assert not resurrected, f"以下字段已声明退役，却又被页面承载: {resurrected}"


def test_retired_fields_exist_in_schema():
    """退役清单只能引用真实存在的字段，否则就是笔误（会静默失去覆盖检查）。"""
    unknown = sorted(_retired_fields() - set(_schema_items()))
    assert not unknown, f"retired 引用了 schema 中不存在的字段: {unknown}"


def test_every_subgroup_renders_something():
    """每个页内分组至少要产出字段，否则页面上会出现空卡片。"""
    empty = [
        f"{section['id']}/{subgroup['id']}"
        for section, subgroup in _all_subgroups()
        if not _subgroup_fields(subgroup)
    ]
    assert not empty, f"以下页内分组不渲染任何字段: {empty}"


def test_section_size_within_budget():
    """单个分区字段数设上限，防止再次长出 42 个字段的巨页。"""
    oversized = {
        section_id: len(fields)
        for section_id, fields in _fields_by_section().items()
        if len(fields) > MAX_FIELDS_PER_SECTION
    }
    assert not oversized, (
        f"以下分区字段过多（上限 {MAX_FIELDS_PER_SECTION}），应拆分为新分区: {oversized}"
    )


def test_basic_section_stays_beginner_friendly():
    """「基础配置」是新手唯一必经页，只允许极少数高级项混入。"""
    basic_fields = _fields_by_section()["basic"]
    assert basic_fields, "基础配置不应为空"
    advanced = {
        key
        for key in basic_fields
        if (SCHEMA.get(key) or {}).get("importance") == "advanced"
    }
    unexpected = sorted(advanced - ADVANCED_ALLOWED_IN_BASIC)
    assert not unexpected, f"基础配置混入了未登记的高级字段，应移到进阶区: {unexpected}"


def test_stream_presets_are_renderable_and_target_the_stream_section():
    """流式预设只有后端一份，且预设改写的字段都落在「流式输出」页里。

    否则「点预设」会静默改掉用户看不见的配置项。
    """
    from app.agent.config_schema import STREAM_PRESETS

    assert STREAM_PRESETS, "STREAM_PRESETS 不能为空，否则前端预设区退化成空壳"

    stream_fields = set(_fields_by_section()["stream"])
    for key, preset in STREAM_PRESETS.items():
        assert preset.get("label"), f"预设 {key} 缺少 label"
        assert "description" in preset, f"预设 {key} 缺少 description"
        config = preset.get("config")
        assert isinstance(config, dict) and config, f"预设 {key} 的 config 不能为空"
        stray = sorted(set(config) - stream_fields)
        assert not stray, f"预设 {key} 改写了不属于「流式输出」页的字段: {stray}"


# ==================== schema 元数据契约 ====================


def test_grouped_fields_carry_importance():
    """importance 是前端区分「基础/进阶」的唯一依据，分组字段必须都带上。"""
    missing = [
        key
        for key, definition in _schema_items().items()
        if definition.get("group") and not definition.get("importance")
    ]
    assert not missing, f"以下字段缺少 importance: {missing}"


# ==================== 条件显隐（showIf）文法契约 ====================
# 前端 ConfigForm.isVisible 与后端 _gate 共用同一套文法；这里把文法钉住，
# 防止有人写出前端解析不了的条件（表现为「设了条件但界面没隐藏/没显示」）。

_SHOW_IF_SIMPLE_KEYS = {"key", "value", "in", "notIn", "gt", "notEmpty"}
_SHOW_IF_JUDGES = {"value", "in", "notIn", "gt", "notEmpty"}


def _iter_conditions(cond):
    """深度遍历条件树，产出所有叶子条件。"""
    if not isinstance(cond, dict):
        yield cond
        return
    for combinator in ("allOf", "anyOf"):
        if combinator in cond:
            for child in cond[combinator]:
                yield from _iter_conditions(child)
            return
    yield cond


def test_show_if_uses_supported_grammar():
    items = _schema_items()
    problems: list[str] = []
    for key, definition in items.items():
        if not definition.get("showIf"):
            continue
        cond = definition["showIf"]
        if not isinstance(cond, dict):
            problems.append(f"{key}: showIf 必须是对象")
            continue
        for combinator in ("allOf", "anyOf"):
            if combinator in cond:
                children = cond[combinator]
                if not isinstance(children, list) or not children:
                    problems.append(f"{key}: {combinator} 必须是非空数组")
                extra = set(cond) - {combinator}
                if extra:
                    problems.append(f"{key}: {combinator} 不能与其它键混用: {sorted(extra)}")
        for leaf in _iter_conditions(cond):
            if not isinstance(leaf, dict):
                problems.append(f"{key}: 条件叶子必须是对象")
                continue
            unknown = set(leaf) - _SHOW_IF_SIMPLE_KEYS
            if unknown:
                problems.append(f"{key}: 未知条件键 {sorted(unknown)}")
            if "key" not in leaf:
                problems.append(f"{key}: 条件缺少 key")
                continue
            if leaf["key"] not in items:
                problems.append(f"{key}: 依赖的字段不存在 {leaf['key']}")
            if not (_SHOW_IF_JUDGES & set(leaf)):
                problems.append(f"{key}: 条件没有判定方式（value/in/notIn/gt/notEmpty）")
    assert not problems, "showIf 条件不合法:\n" + "\n".join(f"  {p}" for p in problems)


def test_show_if_never_references_self():
    for key, definition in _schema_items().items():
        if not definition.get("showIf"):
            continue
        for leaf in _iter_conditions(definition["showIf"]):
            assert leaf.get("key") != key, f"{key} 的 showIf 依赖自己，会永远不显示"


def test_importance_is_field_level():
    """importance 必须是字段级且取值合法（不再按 group 统一打档）。"""
    allowed = {"basic", "advanced"}
    missing = [
        key
        for key, definition in _schema_items().items()
        if definition.get("group") and definition.get("importance") not in allowed
    ]
    assert not missing, f"以下字段的 importance 缺失或非法: {missing}"

    # 指定分组内必须出现分层，否则说明又退回按组打档
    # （历史问题：group_session 24 项全被标 basic）。
    by_group: dict[str, set[str]] = {}
    for definition in _schema_items().values():
        if definition.get("group"):
            by_group.setdefault(definition["group"], set()).add(definition["importance"])
    monolithic = sorted(g for g, marks in by_group.items() if len(marks) == 1 and g in _LAYERED_GROUPS)
    assert not monolithic, f"以下分组仍是单一档位，说明 importance 没做到字段级: {monolithic}"


def test_custom_fields_stay_owned_by_a_subgroup():
    """定制卡片渲染的字段，归属仍需由某个子分组声明。

    否则「persona_preset_id 由人格预设卡片负责」这类声明会悄悄把字段从契约里摘出去：
    表单不渲染它、子分组也不声明它，等于它没有任何页面。
    """
    covered = {
        key for section in _load_sections() for subgroup in section.get("groups") or []
        for key in _subgroup_fields(subgroup)
    }
    problems = [
        f"{section['id']}: {key}"
        for section in _load_sections()
        for key in section.get("customFields") or []
        if key not in covered
    ]
    assert not problems, f"以下 customFields 没有被子分组声明归属: {problems}"


def test_custom_fields_are_not_rendered_twice():
    """被定制卡片接管的字段不应该同时出现在本页 schema 表单里（重复渲染）。"""
    duplicated: list[str] = []
    for section in _load_sections():
        custom = set(section.get("customFields") or [])
        if not custom:
            continue
        # 前端 buildSectionSchema 会把 customFields 从 items 里剔除；
        # 这里只校验「声明了 customFields 就说明该页有定制组件」这一前提成立。
        assert section.get("customFirst") is not None, f"{section['id']} 声明 customFields 但没定制组件"
        for subgroup in section["groups"]:
            share = custom & set(subgroup.get("fields") or [])
            if share and len(subgroup.get("fields") or []) == len(share):
                duplicated.append(f"{section['id']}/{subgroup['id']}")
    assert not duplicated, f"以下子分组只包含定制字段，会在表单里渲染成空分组: {duplicated}"
