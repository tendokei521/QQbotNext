"""Agent 分区 registry 与后端配置 schema 的跨语言契约测试。

前端 ``dashboard/src/agent/sections.json`` 是「哪些 group 出现在哪个分区」的唯一权威：
路由、侧边栏二级菜单、概览卡片都由它派生。后端 ``app/llm/config_schema.py`` 里的
136 个字段必须**恰好**被某个分区覆盖一次，否则就会出现「配置项在 UI 里没有入口」
或「同一配置项被两个分区重复渲染」。

这类漂移以前靠人肉同步（分区清单曾抄在 5 处），这里用测试兜住。
"""

from __future__ import annotations

import json
from pathlib import Path

from app.llm.config_schema import SCHEMA

SECTIONS_PATH = (
    Path(__file__).resolve().parents[1] / "dashboard" / "src" / "agent" / "sections.json"
)

# 单个分区承载的字段上限：超过就该再拆一页。
# 历史上「基础配置」一页塞了 42 个字段，是这次重划的直接动因。
MAX_FIELDS_PER_SECTION = 30


def _load_sections() -> list[dict]:
    data = json.loads(SECTIONS_PATH.read_text(encoding="utf-8"))
    return data["sections"]


def _field_groups() -> dict[str, str]:
    """字段 key -> schema group（无 group 的字段不收进映射）。"""
    return {
        key: definition["group"]
        for key, definition in SCHEMA.items()
        if isinstance(definition, dict) and definition.get("group")
    }


def _sections_for_field(key: str, group: str, sections: list[dict]) -> list[str]:
    """复刻前端 agentSectionForField：显式 fields 命中优先，其次按 group 归属。"""
    by_field = [s["id"] for s in sections if key in (s.get("fields") or [])]
    if by_field:
        return by_field
    return [s["id"] for s in sections if group in (s.get("groups") or [])]


def _fields_by_section() -> dict[str, list[str]]:
    """分区 id -> 该分区实际渲染的字段（含 fields 独占的剔除规则）。"""
    sections = _load_sections()
    carved = {f for s in sections for f in (s.get("fields") or [])}
    result: dict[str, list[str]] = {}
    for section in sections:
        if section.get("fields"):
            owned = [k for k in SCHEMA if k in set(section["fields"])]
        else:
            groups = set(section.get("groups") or [])
            owned = [
                key
                for key, definition in SCHEMA.items()
                if isinstance(definition, dict)
                and definition.get("type") != "group"
                and definition.get("group") in groups
                and key not in carved
            ]
        result[section["id"]] = owned
    return result


def test_sections_manifest_exists_and_parses():
    assert SECTIONS_PATH.is_file(), f"缺少分区清单: {SECTIONS_PATH}"
    assert _load_sections(), "分区清单不能为空"


def test_section_ids_are_unique():
    ids = [s["id"] for s in _load_sections()]
    assert len(ids) == len(set(ids)), f"分区 id 重复: {ids}"


def test_section_paths_are_unique():
    paths = [s["path"] for s in _load_sections()]
    assert len(paths) == len(set(paths)), f"分区路径重复: {paths}"


def test_every_section_has_required_metadata():
    for section in _load_sections():
        for key in ("id", "path", "title", "subtitle", "icon", "color", "groups"):
            assert section.get(key) not in (None, ""), f"分区 {section.get('id')} 缺字段 {key}"
        assert section["path"].startswith("/"), f"分区 {section['id']} 路径应以 / 开头"


def test_section_area_matches_path():
    """归属区域决定路由由谁生成：agent 必须挂 /agent/*，tools 必须在别处。"""
    for section in _load_sections():
        area = section.get("area", "agent")
        if area == "agent":
            assert section["path"].startswith("/agent/"), (
                f"分区 {section['id']} 属于 agent 区域，路径却不在 /agent/ 下"
            )
        else:
            assert not section["path"].startswith("/agent/"), (
                f"分区 {section['id']} 归属 {area} 区域，不应挂在 /agent/ 下"
            )


def test_sections_only_reference_existing_groups():
    known = set(_field_groups().values())
    for section in _load_sections():
        for group in section.get("groups") or []:
            assert group in known, f"分区 {section['id']} 引用了不存在的 group: {group}"


def test_sections_only_reference_existing_fields():
    for section in _load_sections():
        for key in section.get("fields") or []:
            assert key in SCHEMA, f"分区 {section['id']} 引用了不存在的字段: {key}"


def test_every_field_is_covered_exactly_once():
    """核心契约：每个配置字段都必须有且只有一个分区出口。"""
    sections = _load_sections()
    unmapped: list[str] = []
    duplicated: dict[str, list[str]] = {}

    for key, group in _field_groups().items():
        owners = _sections_for_field(key, group, sections)
        if not owners:
            unmapped.append(f"{key}({group})")
        elif len(owners) > 1:
            duplicated[key] = owners

    assert not unmapped, f"以下字段没有任何分区承载（UI 里将无法配置）: {unmapped}"
    assert not duplicated, f"以下字段被多个分区重复承载: {duplicated}"


def test_every_group_is_owned_by_exactly_one_section():
    """分组不能被两个分区同时认领，否则同一批字段会出现两次。"""
    sections = _load_sections()
    owners: dict[str, list[str]] = {}
    for section in sections:
        for group in section.get("groups") or []:
            owners.setdefault(group, []).append(section["id"])
    shared = {g: ids for g, ids in owners.items() if len(ids) > 1}
    assert not shared, f"以下分组被多个分区同时认领: {shared}"

    unclaimed = set(_field_groups().values()) - set(owners)
    assert not unclaimed, f"以下分组没有任何分区认领: {sorted(unclaimed)}"


def test_sections_are_not_empty():
    """每个分区至少要真正渲染点东西，否则就是个空壳页面。"""
    by_section = _fields_by_section()
    empty = [
        section["id"]
        for section in _load_sections()
        if not by_section[section["id"]] and not section.get("custom")
    ]
    assert not empty, f"以下分区不渲染任何字段（且未声明 custom 组件）: {empty}"


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
    """「基础开关」必须全部是 basic 档字段——它是新手唯一的必经页。"""
    by_section = _fields_by_section()
    basic_fields = by_section["basic"]
    assert basic_fields, "基础分区不应为空"
    advanced = [
        key
        for key in basic_fields
        if (SCHEMA.get(key) or {}).get("importance") == "advanced"
    ]
    assert not advanced, f"基础分区混入了 advanced 档字段，应移到进阶分区: {advanced}"


def test_stream_presets_are_renderable_and_target_the_stream_section():
    """流式预设只有后端一份（前端曾硬编码重复副本，已随本次重构删除）。

    这里保证后端下发的预设结构完整，且预设改写的字段都落在「流式回复」分区里——
    否则「点预设」会静默改掉用户看不见的配置项。
    """
    from app.llm.config_schema import STREAM_PRESETS

    assert STREAM_PRESETS, "STREAM_PRESETS 不能为空，否则前端预设区退化成空壳"

    stream_fields = set(_fields_by_section()["stream"])
    for key, preset in STREAM_PRESETS.items():
        assert preset.get("label"), f"预设 {key} 缺少 label"
        assert "description" in preset, f"预设 {key} 缺少 description"
        config = preset.get("config")
        assert isinstance(config, dict) and config, f"预设 {key} 的 config 不能为空"
        stray = sorted(set(config) - stream_fields)
        assert not stray, f"预设 {key} 改写了不属于「流式回复」分区的字段: {stray}"



def test_grouped_fields_carry_importance():
    """importance 是前端区分「基础/进阶分区」的唯一依据，分组字段必须都带上。"""
    missing = [
        key
        for key, definition in SCHEMA.items()
        if isinstance(definition, dict) and definition.get("group") and not definition.get("importance")
    ]
    assert not missing, f"以下字段缺少 importance 标注: {missing}"


def test_schema_no_longer_carries_page_metadata():
    """分区归属的唯一权威是前端 registry，后端不再逐字段标注 page。

    历史上 page 与 registry 是两份真相：重划分区时 page 会滞留旧值，
    「搜索配置」就会跳到不含该字段的页面（重划时确实踩到过）。
    """
    offenders = [
        key
        for key, definition in SCHEMA.items()
        if isinstance(definition, dict) and "page" in definition
    ]
    assert not offenders, f"以下字段仍带 page 元数据，应删除（registry 才是权威）: {offenders}"
