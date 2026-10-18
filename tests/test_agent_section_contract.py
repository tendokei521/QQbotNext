"""Agent 分区 registry 与后端配置 schema 的跨语言契约测试。

前端 ``dashboard/src/agent/sections.json`` 是「哪些 group 出现在哪个分区」的唯一权威：
路由、侧边栏二级菜单、概览卡片都由它派生。后端 ``app/llm/config_schema.py`` 里的
136 个字段必须**恰好**被某个分区覆盖一次，否则就会出现「配置项在 UI 里没有入口」
或「同一配置项被两个分区重复渲染」。

这类漂移以前靠人肉同步（分区清单被抄在 5 处），这里用测试兜住。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.llm.config_schema import SCHEMA

SECTIONS_PATH = (
    Path(__file__).resolve().parents[1] / "dashboard" / "src" / "agent" / "sections.json"
)


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


def test_sections_manifest_exists_and_parses():
    assert SECTIONS_PATH.is_file(), f"缺少分区清单: {SECTIONS_PATH}"
    sections = _load_sections()
    assert sections, "分区清单不能为空"


def test_section_ids_are_unique():
    sections = _load_sections()
    ids = [s["id"] for s in sections]
    assert len(ids) == len(set(ids)), f"分区 id 重复: {ids}"


def test_section_paths_are_unique():
    sections = _load_sections()
    paths = [s["path"] for s in sections]
    assert len(paths) == len(set(paths)), f"分区路径重复: {paths}"


def test_every_section_has_required_metadata():
    for section in _load_sections():
        for key in ("id", "path", "title", "subtitle", "icon", "color", "groups"):
            assert section.get(key) not in (None, ""), f"分区 {section.get('id')} 缺字段 {key}"
        assert section["path"].startswith("/"), f"分区 {section['id']} 路径应以 / 开头"


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


def test_sections_are_not_empty():
    """每个分区至少要真正渲染点东西，否则就是个空壳页面。"""
    sections = _load_sections()
    field_groups = _field_groups()
    empty: list[str] = []
    for section in sections:
        owners = [
            key
            for key, group in field_groups.items()
            if section["id"] in _sections_for_field(key, group, sections)
        ]
        if not owners and not section.get("custom"):
            empty.append(section["id"])
    assert not empty, f"以下分区不渲染任何字段（且未声明 custom 组件）: {empty}"


@pytest.mark.parametrize("section_id", [s["id"] for s in _load_sections()])
def test_section_id_is_valid_page_metadata_value(section_id: str):
    """分区 id 必须仍能对应后端 schema 的 page 取值，保证深链/搜索跳转不失效。"""
    pages = {
        definition.get("page")
        for definition in SCHEMA.values()
        if isinstance(definition, dict)
    }
    # role 这类纯 fields 分区不会有字段的 page 指向它，需放行
    sections = _load_sections()
    section = next(s for s in sections if s["id"] == section_id)
    if section.get("fields"):
        return
    assert section_id in pages, (
        f"分区 {section_id} 没有对应任何 schema page 元数据；"
        "若已改为纯 group 驱动，请同步移除对 page 的依赖"
    )
