"""资源字段的悬挂引用策略（`on_missing`）：保存配置时校验引用的资源还在不在。

`type: "resource"` 的值是资源 id；资源被删后配置里会留下悬挂 id。

- 默认策略 ``keep``：原样保留（前端显示「`<id>`（已删除或未找到）」，用户自己决定）；
- ``on_missing: "clear"``：**保存时丢弃**该值（置 ``""`` / 空数组），避免模块长期拿着
  一个永远解析不到的 id 运行。

为什么放在**保存**路径而不是读取路径：读取路径（``get_modules_data``）是同步的，而候选
拉取可能是异步的；而且改数据应当发生在明确的写操作里，不该在 GET 里产生副作用。
这条 API 同时服务新版 Dashboard 与 /legacy，所以两套界面的行为天然一致。
"""

from __future__ import annotations

from typing import Any

from app.core.logger import logger

#: 支持悬挂引用策略的字段类型
RESOURCE_TYPES = ("resource", "resources")


async def apply_on_missing(schema: dict | None, data: dict, registry: Any) -> tuple[dict, list[str]]:
    """按 schema 校验资源字段的悬挂引用。

    Args:
        schema: 模块/Agent 的 config_schema（读 ``on_missing`` / ``resource`` / ``filters``）。
        data: 本次提交的配置（浅拷贝后处理，不改调用方对象）。
        registry: ``ResourceRegistry``（``has`` 同步查种类、``options`` 拉候选）。

    Returns:
        ``(处理后的配置, 被清空的字段名列表)``；未声明 ``on_missing: clear``、
        种类未注册、候选拉取失败时**保持原值**（宁可保留也不误删用户配置）。
    """
    if not isinstance(data, dict) or not isinstance(schema, dict) or registry is None:
        return data, []

    result = dict(data)
    cleared: list[str] = []

    for key, field in schema.items():
        if not isinstance(field, dict):
            continue
        ftype = str(field.get("type") or "").lower()
        if ftype not in RESOURCE_TYPES:
            continue
        if str(field.get("on_missing") or "keep").lower() != "clear":
            continue
        kind = str(field.get("resource") or "").strip()
        if not kind or not hasattr(registry, "has") or not registry.has(kind):
            continue
        if key not in result:
            continue

        value = result[key]
        multiple = ftype == "resources"
        if multiple:
            submitted = [str(v) for v in value] if isinstance(value, list) else []
        else:
            submitted = [] if value in (None, "") else [str(value)]
        if not submitted:
            continue

        try:
            options = await registry.options(kind, filters=field.get("filters") or {})
        except Exception as e:  # noqa: BLE001 —— 拉候选失败时保留原值，别误删配置
            logger.warning(f"[Resource] 校验字段 {key} 的悬挂引用失败（保留原值）: {e}")
            continue
        available = {str(item.get("value")) for item in options or [] if isinstance(item, dict)}

        if multiple:
            kept = [v for v in submitted if v in available]
            if len(kept) != len(submitted):
                result[key] = kept
                cleared.append(key)
        elif submitted[0] not in available:
            result[key] = ""
            cleared.append(key)

    return result, cleared
