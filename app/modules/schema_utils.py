"""模块配置 schema 的归一化与校验（单一事实源）。

此前 ``_split_schema`` 在 `app/services/bot_service.py` 与 `app/webui/api/agent.py` 各写一遍，
且 schema **从不校验**：未知 ``type`` 会被前端兜底成文本框、``type: "resource"`` 的
``resource`` 种类写错只表现为"空下拉"——这类问题极难排查。

这里集中做四件事，并让诊断随 schema 一起下发（``issues`` 字段，前端可显示、后端可断言）：

1. ``normalize_schema``：类型白名单、label 兜底、resource 种类校验、选项/模板形状校验；
2. ``split_schema``：分组与字段分区（与两处旧实现同语义）；
3. ``webui_schema``：一步到位（归一化 + 分区），WebUI 与 bot_service 统一走它；
4. ``schema_issues``：汇总一份 schema 的全部问题（供能力/诊断端点用）。
"""

from __future__ import annotations

import copy
from typing import Any

#: schema 契约版本：新增类型/改变字段语义时递增（模块可据此判断框架能否渲染）
SCHEMA_VERSION = 2
#: 与前端两套渲染器一致的基础类型（新增类型请先加进这里，否则会被标 unknown_type）
SIMPLE_TYPES = (
    "text", "string", "password", "number", "integer", "float",
    "boolean", "bool", "select", "textarea", "time",
)
#: 需要独立组件渲染的复杂类型
COMPLEX_TYPES = ("string_list", "list", "dynamic", "repeater")
#: 资源引用类型（见 ResourceRegistry）：值是资源 id，前端渲染成下拉选择器
RESOURCE_TYPES = ("resource", "resources")
#: 全部已知类型（含结构型 group）
KNOWN_TYPES = (*SIMPLE_TYPES, *COMPLEX_TYPES, *RESOURCE_TYPES, "group")


def normalize_field(
    key: str,
    raw: Any,
    *,
    registry: Any = None,
    source_module: str = "",
) -> dict:
    """归一化单个字段定义，并把诊断写进 ``issues``（列表，恒存在）。

    不做「静默修正」：类型不认识就只在 ``issues`` 里说明，仍按声明下发——这样
    新前端能渲染、旧前端自行兜底，而问题不会消失得无影无踪。
    """
    field: dict = copy.deepcopy(raw) if isinstance(raw, dict) else {"type": "text"}
    ftype = str(field.get("type") or "text").strip().lower()
    field["type"] = ftype
    if not str(field.get("label") or "").strip():
        field["label"] = key  # label 兜底：前端不必处理空标签

    issues: list[str] = [str(x) for x in (field.get("issues") or [])]
    if ftype not in KNOWN_TYPES:
        issues.append(f"unknown_type:{ftype}")

    if ftype in RESOURCE_TYPES:
        kind = str(field.get("resource") or "").strip()
        if not kind:
            issues.append("resource_kind_missing")
        elif registry is not None and hasattr(registry, "has") and not registry.has(kind):
            issues.append(f"unknown_resource:{kind}")

    if ftype == "select":
        options = field.get("options")
        if options is None:
            issues.append("select_without_options")
        elif not isinstance(options, (dict, list)):
            issues.append("select_options_bad_shape")

    if ftype == "repeater" and not isinstance(field.get("template"), dict):
        issues.append("repeater_without_template")

    if source_module:
        field.setdefault("source_module", source_module)
    field["issues"] = issues
    return field


def normalize_schema(
    schema: dict | None,
    *,
    registry: Any = None,
    source_module: str = "",
) -> dict:
    """归一化整份 schema（返回新字典，**不改动**模块类属性上的原 schema）。"""
    return {
        str(key): normalize_field(str(key), value, registry=registry, source_module=source_module)
        for key, value in (schema or {}).items()
    }


def split_schema(schema: dict | None) -> dict:
    """按 ``type == "group"`` 分成 ``{"groups", "items"}``（与原两处实现同语义）。"""
    groups: dict = {}
    items: dict = {}
    for key, value in (schema or {}).items():
        if isinstance(value, dict) and value.get("type") == "group":
            groups[key] = value
        else:
            items[key] = value
    return {"groups": groups, "items": items}


def webui_schema(schema: dict | None, *, registry: Any = None, source_module: str = "") -> dict:
    """WebUI 下发的形状：先归一化（含诊断），再分区。"""
    return split_schema(normalize_schema(schema, registry=registry, source_module=source_module))


def schema_issues(normalized_schema: dict | None) -> list[dict]:
    """汇总已归一化 schema 里的问题：``[{"key", "type", "issues"}]``（无问题为空列表）。"""
    rows: list[dict] = []
    for key, field in (normalized_schema or {}).items():
        if not isinstance(field, dict):
            continue
        issues = [str(x) for x in (field.get("issues") or [])]
        if issues:
            rows.append({"key": key, "type": str(field.get("type") or ""), "issues": issues})
    return rows
