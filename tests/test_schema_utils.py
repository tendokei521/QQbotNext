"""配置 schema 的归一化与校验（`app/plugin/schema_utils.py`）。

背景：`_split_schema` 原先在两处各写一遍，且 schema 从不校验——未知 type 会被前端
兜底成文本框、`type: "resource"` 的种类写错只表现为"空下拉"。这里锁住新口径：

1. 类型白名单与诊断（`issues`）随 schema 下发，不静默修正；
2. `label` 兜底为字段键；非 dict 声明按文本字段处理；
3. `resource` 校验种类是否已在 ResourceRegistry 注册（未注册 → `unknown_resource:`）；
4. `select` 选项形状、`repeater` 模板形状校验；
5. 归一化**不改动**模块类属性上的原 schema（深拷贝），并注入 `source_module`；
6. `webui_schema` 一步给出 WebUI 形状，bot_service 与 agent API 共用。
"""

from __future__ import annotations

from app.plugin.schema_utils import (
    KNOWN_TYPES,
    normalize_field,
    normalize_schema,
    schema_issues,
    split_schema,
    webui_schema,
)
from app.services.resource_registry import ResourceRegistry


class _FakeRegistry:
    def __init__(self, kinds):
        self._kinds = set(kinds)

    def has(self, kind):
        return kind in self._kinds


def _registry():
    return _FakeRegistry({"role_preset", "provider_model"})


# ==================== 类型白名单 ====================


def _minimal(type_name: str) -> dict:
    """每种类型的最小合法声明（校验类类型需要它要求的附属字段）。"""
    if type_name in ("resource", "resources"):
        return {"type": type_name, "resource": "role_preset"}
    if type_name == "select":
        return {"type": "select", "options": {"a": "A"}}
    if type_name == "repeater":
        return {"type": "repeater", "template": {}}
    return {"type": type_name}


def test_known_types_have_no_issues():
    """白名单里的类型用最小合法声明都不该报 issue（白名单与校验规则自洽）。"""
    schema = {name: _minimal(name) for name in KNOWN_TYPES}
    normalized = normalize_schema(schema, registry=_registry())
    assert {key: field["issues"] for key, field in normalized.items() if field["issues"]} == {}


def test_unknown_type_is_reported_not_silently_replaced():
    field = normalize_field("weird", {"type": "wysiwyg"})
    assert field["type"] == "wysiwyg", "类型保持原声明（旧前端自行兜底，新前端可渲染）"
    assert field["issues"] == ["unknown_type:wysiwyg"]


def test_missing_type_defaults_to_text():
    field = normalize_field("k", {"label": "无类型"})
    assert field["type"] == "text" and field["issues"] == []


def test_non_dict_field_declaration_is_text():
    field = normalize_field("k", "本来是字符串")
    assert field == {"type": "text", "label": "k", "issues": []}


def test_label_falls_back_to_key():
    assert normalize_field("group_id", {"type": "string"})["label"] == "group_id"
    assert normalize_field("k", {"type": "string", "label": "  "})["label"] == "k"
    assert normalize_field("k", {"type": "string", "label": "真标签"})["label"] == "真标签"


# ==================== resource 校验 ====================


def test_resource_kind_registered_ok():
    field = normalize_field("persona", {"type": "resource", "resource": "role_preset"}, registry=_registry())
    assert field["issues"] == []


def test_resource_unknown_kind_reported():
    field = normalize_field("persona", {"type": "resource", "resource": "nope"}, registry=_registry())
    assert field["issues"] == ["unknown_resource:nope"]


def test_resource_missing_kind_reported():
    field = normalize_field("persona", {"type": "resource"}, registry=_registry())
    assert field["issues"] == ["resource_kind_missing"]


def test_resource_plural_type_is_recognized():
    field = normalize_field("pool", {"type": "resources", "resource": "provider_model"}, registry=_registry())
    assert field["issues"] == [], "resources（多选）也是已知类型"


def test_resource_without_registry_skips_kind_check():
    """没给注册表时只校验"有没有写 kind"，不误报未注册。"""
    field = normalize_field("persona", {"type": "resource", "resource": "whatever"})
    assert field["issues"] == []


# ==================== 其它形状校验 ====================


def test_select_shape_checks():
    assert normalize_field("m", {"type": "select", "options": {"a": "A"}})["issues"] == []
    assert normalize_field("m", {"type": "select", "options": ["a"]})["issues"] == []
    assert normalize_field("m", {"type": "select"})["issues"] == ["select_without_options"]
    assert normalize_field("m", {"type": "select", "options": "a"})["issues"] == ["select_options_bad_shape"]


def test_repeater_requires_template():
    assert normalize_field("r", {"type": "repeater", "template": {}})["issues"] == []
    assert normalize_field("r", {"type": "repeater"})["issues"] == ["repeater_without_template"]


def test_issues_accumulate_and_are_preserved():
    field = normalize_field("x", {"type": "resource", "resource": "nope", "issues": ["自定义问题"]}, registry=_registry())
    assert field["issues"] == ["自定义问题", "unknown_resource:nope"]


# ==================== 不污染原 schema / 注入来源 ====================


def test_normalize_does_not_mutate_original_schema():
    original = {"a": {"type": "select"}, "tpl": {"type": "repeater", "template": {"k": {"type": "string"}}}}
    normalized = normalize_schema(original, source_module="demo")
    assert original["a"] == {"type": "select"}, "原 schema 不得被写入 issues/label"
    assert "source_module" not in original["tpl"]
    # 深拷贝：改动归一化结果里的嵌套模板不该影响原 schema
    normalized["tpl"]["template"]["k"]["type"] = "number"
    assert original["tpl"]["template"]["k"]["type"] == "string"


def test_source_module_injected_only_when_given():
    assert normalize_field("k", {"type": "text"}, source_module="demo")["source_module"] == "demo"
    assert "source_module" not in normalize_field("k", {"type": "text"})


def test_explicit_source_module_is_kept():
    field = normalize_field("k", {"type": "text", "source_module": "self"}, source_module="demo")
    assert field["source_module"] == "self"


# ==================== 分区与 WebUI 形状 ====================


def test_split_schema_keeps_group_semantics():
    schema = {"g": {"type": "group", "label": "组"}, "a": {"type": "text"}}
    assert split_schema(schema) == {"groups": {"g": {"type": "group", "label": "组"}}, "items": {"a": {"type": "text"}}}
    assert split_schema(None) == {"groups": {}, "items": {}}


def test_webui_schema_composes_normalize_and_split():
    schema = {"g": {"type": "group"}, "bad": {"type": "nope"}}
    out = webui_schema(schema, registry=_registry(), source_module="demo")
    assert set(out) == {"groups", "items"}
    assert out["groups"]["g"]["label"] == "g"
    assert out["items"]["bad"]["issues"] == ["unknown_type:nope"]
    assert out["items"]["bad"]["source_module"] == "demo"


def test_schema_issues_summary():
    normalized = normalize_schema({"ok": {"type": "text"}, "bad": {"type": "nope"}})
    assert schema_issues(normalized) == [{"key": "bad", "type": "nope", "issues": ["unknown_type:nope"]}]
    assert schema_issues({}) == []


# ==================== 真实 schema 与集成 ====================


def test_agent_schema_normalizes_without_issues(container):
    """框架自带 Agent schema 应当零 issue（否则是它自己写错了类型）。"""
    from app.agent.config_schema import SCHEMA

    normalized = normalize_schema(SCHEMA, registry=container.get(ResourceRegistry), source_module="agent")
    assert schema_issues(normalized) == []


def test_module_schemas_normalize_without_issues(container):
    """全部已装模块的 schema 也应零 issue（老模块零改动仍然可用）。"""
    import importlib
    from pathlib import Path

    repo_modules = Path(__file__).resolve().parent.parent / "app" / "plugins"
    resource_registry = container.get(ResourceRegistry)
    problems: dict = {}
    for entry in sorted(p for p in repo_modules.iterdir() if p.is_dir()):
        if not (entry / "module.py").exists():
            continue
        try:
            schema_mod = importlib.import_module(f"app.plugins.{entry.name}.config_schema")
        except Exception:  # noqa: BLE001 —— 没写/导入不了的模块跳过
            continue
        schema = getattr(schema_mod, "SCHEMA", None)
        if not isinstance(schema, dict):
            continue
        found = schema_issues(
            normalize_schema(schema, registry=resource_registry, source_module=entry.name)
        )
        if found:
            problems[entry.name] = found
    assert problems == {}, f"这些模块的 schema 有 issue: {problems}"
