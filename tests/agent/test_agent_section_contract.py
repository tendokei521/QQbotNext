"""前端分区清单契约：`sections.json` 必须由 `page_summary()` 生成且与本体重合。

## v3 之后这份测试守什么

1. **单一真相**：`sections.json` 必须与 `scripts/gen_sections.py` 的输出**逐字节一致**
   —— 手改清单会立刻失败，避免"前端显示的字段"与"后端 schema"漂移；
2. **字段归属唯一**：43 个本体键每个恰好出现在一个分组里；
3. **不出现模块键**：已迁到 9 个模块的 108 键、已删除的 9 键都不得出现在本体清单；
4. **分组可渲染**：每个分组至少一个字段（否则前端渲染空卡片）。

> 历史：v2 时清单是手写的 9 分区 / 160 字段，本文件校验的是"手写清单与 schema 的一致性"。
> v3 起清单由代码生成，校验重点转为"生成结果是否被正确提交"。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SECTIONS_PATH = ROOT / "app" / "webui" / "dashboard" / "src" / "agent" / "sections.json"

_SPEC = importlib.util.spec_from_file_location(
    "gen_sections", ROOT / "scripts" / "gen_sections.py"
)
assert _SPEC and _SPEC.loader
gen_sections = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gen_sections)

from app.agent.config_partition import CORE_KEYS, DELETED_KEYS, MODULE_KEYS  # noqa: E402
from app.agent.config_schema import SCHEMA  # noqa: E402


def _load() -> dict:
    return json.loads(SECTIONS_PATH.read_text(encoding="utf-8"))


def _sections() -> list[dict]:
    return _load()["sections"]


def _fields_of(section: dict) -> list[str]:
    return [f for group in section["groups"] for f in group["fields"]]


def _all_fields() -> list[str]:
    return [f for section in _sections() for f in _fields_of(section)]


# ---------- 单一真相 ----------


def test_manifest_is_generated_not_handwritten():
    """清单必须等于生成器输出（改字段请改 config_partition.py 再重新生成）。"""
    current = SECTIONS_PATH.read_text(encoding="utf-8")
    expected = gen_sections.render(gen_sections.build_manifest())
    assert current == expected, (
        "sections.json 与 page_summary() 不一致；请运行 python scripts/gen_sections.py"
    )


def test_manifest_declares_its_source():
    manifest = _load()
    assert manifest.get("version") == 5
    assert "config_partition" in manifest.get("generatedFrom", "")


def test_manifest_has_no_area_field():
    """v5 起只有单一区域；`area` 会让已删的 /advanced 在代码里残留脚手架。"""
    for section in _sections():
        assert "area" not in section, f"{section['id']} 仍带 area 字段"


# ---------- 页面结构 ----------


def test_exactly_five_pages_in_agreed_order():
    ids = [s["id"] for s in _sections()]
    assert ids == ["basic", "permission", "behavior", "session", "advanced"]


def test_page_titles_match_the_agreed_layout():
    titles = {s["id"]: s["title"] for s in _sections()}
    assert titles == {
        "basic": "基础配置",
        "permission": "权限管理",
        "behavior": "对话行为",
        "session": "会话管理",
        "advanced": "高级功能",
    }


@pytest.mark.parametrize(
    ("section_id", "expected"),
    [
        ("basic", 4),
        ("permission", 3),
        ("behavior", 8),
        ("session", 13),
        ("advanced", 12),
    ],
)
def test_section_field_counts(section_id, expected):
    section = next(s for s in _sections() if s["id"] == section_id)
    assert len(_fields_of(section)) == expected


def test_every_section_is_renderable():
    for section in _sections():
        assert section["path"].startswith("/agent/"), f"{section['id']} 路由异常"
        assert section["title"] and section["icon"], f"{section['id']} 缺标题或图标"
        for group in section["groups"]:
            assert group["fields"], f"{section['id']}/{group['id']} 是空分组（会渲染空卡片）"
            assert group["label"] and group["icon"], f"{group['id']} 缺 label 或 icon"


def test_group_ids_are_unique():
    ids = [g["id"] for s in _sections() for g in s["groups"]]
    assert len(ids) == len(set(ids))


# ---------- 字段归属 ----------


def test_every_ui_field_appears_exactly_once():
    fields = _all_fields()
    dup = sorted({f for f in fields if fields.count(f) > 1})
    assert not dup, f"这些字段在清单里出现多次: {dup}"


def test_manifest_covers_exactly_the_core_keys():
    assert set(_all_fields()) == set(CORE_KEYS)


def test_manifest_never_lists_module_or_deleted_keys():
    migrated = {key for keys in MODULE_KEYS.values() for key in keys}
    listed = set(_all_fields())
    assert not (listed & migrated), f"模块键不该出现在本体清单: {sorted(listed & migrated)}"
    assert not (listed & set(DELETED_KEYS)), f"已删除键不该出现: {sorted(listed & set(DELETED_KEYS))}"


def test_every_listed_field_exists_in_schema():
    schema_keys = {
        key
        for key, value in SCHEMA.items()
        if isinstance(value, dict) and value.get("type") != "group"
    }
    missing = sorted(set(_all_fields()) - schema_keys)
    assert not missing, f"清单里有 schema 不存在的字段: {missing}"


def test_owner_roster_lives_on_permission_page():
    permission = next(s for s in _sections() if s["id"] == "permission")
    assert "agent_owner_list" in _fields_of(permission)
    assert "agent_manager_list" in _fields_of(permission)
