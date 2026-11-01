"""存量配置自愈：把「schema 判为非法」的值纠正成默认值。

## 为什么需要

模块时代某些键的取值集合比现在宽（例如 `stream_send_curve` 曾允许 `"none"`）。
模块退役、键搬回 agent 配置后，schema 枚举收紧，于是**用户什么都没改、一保存就
报「值不合法，未保存」**——因为前端会把整份草稿（含这些脏值）回传。

保存路径必须拒绝非法值（否则脏数据会继续扩散），所以脏值只能在**启动时**清掉：
这里扫一遍 agent 配置，把 select 值不在 options、number 越界、bool 类型错的键
重置成 schema 默认值，并打日志。幂等：合法值一个都不动。
"""

from __future__ import annotations

from typing import Any

from app.core.logger import logger

#: 内部哨兵：表示"这个值合法，不需要改"
_UNCHANGED = object()


def _fixed_value(spec: dict, value: Any) -> Any:
    """返回应写入的修正值；合法则返回 ``_UNCHANGED``。"""
    kind = spec.get("type")
    if kind == "select":
        options = spec.get("options") or {}
        if not options:
            return _UNCHANGED
        # 允许"空值 = 用默认"（部分可选下拉允许留空）
        if value in options or value in ("", None):
            return _UNCHANGED
        default = spec.get("default")
        return default if default in options else next(iter(options))
    if kind == "number":
        try:
            number = float(value)
        except (TypeError, ValueError):
            return spec.get("default", 0)
        low, high = spec.get("min"), spec.get("max")
        if (low is not None and number < float(low)) or (high is not None and number > float(high)):
            return spec.get("default", 0)
        return _UNCHANGED
    if kind == "boolean":
        if isinstance(value, bool) or value in (0, 1, "true", "false"):
            return _UNCHANGED
        return bool(spec.get("default", False))
    return _UNCHANGED


async def heal_agent_config(config_service: Any) -> dict[str, list[str]]:
    """纠正 agent 配置里的非法值，返回 ``{scope: [修正说明, ...]}``。"""
    from app.agent.config_schema import SCHEMA

    fixed: dict[str, list[str]] = {}
    try:
        rows = config_service.list_bindings("agent")
    except Exception as e:  # noqa: BLE001 —— 自愈失败不该阻断启动
        logger.error(f"[Heal] 读取 agent 配置失败（跳过自愈）: {e}")
        return fixed

    for row in rows:
        scope_type = str(row.get("scope_type") or "account")
        scope_id = str(row.get("scope_id") or "")
        cfg = row.get("config")
        if not isinstance(cfg, dict) or not cfg:
            continue
        changed: list[str] = []
        for key, spec in SCHEMA.items():
            if key not in cfg or not isinstance(spec, dict):
                continue
            before = cfg[key]
            after = _fixed_value(spec, before)
            if after is _UNCHANGED:
                continue
            cfg[key] = after
            changed.append(f"{key}: {before!r} -> {after!r}")
        if not changed:
            continue
        try:
            await config_service.save_binding(
                "agent",
                scope_type,
                scope_id,
                enabled=bool(row.get("enabled", True)),
                config=cfg,
            )
            fixed[f"{scope_type}:{scope_id}"] = changed
        except Exception as e:  # noqa: BLE001 —— 单个作用域失败不影响其它
            logger.error(f"[Heal] 修正 {scope_type}:{scope_id} 失败: {e}")

    if fixed:
        reloader = getattr(config_service, "_load_bindings", None)
        if callable(reloader):
            await reloader()
        for scope, changes in fixed.items():
            logger.warning(
                f"[Heal] {scope} 的配置有 {len(changes)} 个非法值已重置为默认"
                f"（否则保存会被拒绝）：" + "；".join(changes)
            )
    return fixed


__all__ = ["heal_agent_config"]
