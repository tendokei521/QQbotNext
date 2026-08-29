"""NapCat 工具策略解析：总开关 / 白黑名单 / 覆盖权限 / 敏感度拦截。"""

from __future__ import annotations

from typing import Any

PERMISSION_PATH = ("everyone", "member", "group_admin", "group_owner", "owner")


def _overrides(runtime) -> dict:
    if runtime is None:
        return {}
    try:
        config = getattr(runtime, "config", None)
        raw = config.get("napcat_tool_overrides", {}) if config is not None else {}
        return dict(raw or {})
    except Exception:
        return {}


def resolve_tool_policy(runtime, tool: dict) -> dict:
    """返回某个 NapCat 工具的最终策略。"""
    name = str(tool.get("name", "") or "")
    base_permission = str(tool.get("permission", "member") or "member")
    base_scopes = list(tool.get("scopes", ["*"]) or ["*"])
    sensitivity = str(tool.get("sensitivity", "normal") or "normal")
    risk = str(tool.get("risk", "normal") or "normal")

    try:
        config = getattr(runtime, "config", None)
        enabled = bool(config.get("napcat_tools_enable", False)) if config is not None else False
        denied = list(config.get("napcat_tools_denied", []) or []) if config is not None else []
        allowed = list(config.get("napcat_tools_allowed", []) or []) if config is not None else []
    except Exception:
        enabled = False
        denied = []
        allowed = []

    overrides = _overrides(runtime)
    override = overrides.get(name, {}) or {}
    permission = str(override.get("permission", base_permission) or base_permission)
    scopes = list(override.get("scopes", base_scopes) or base_scopes)
    if permission not in PERMISSION_PATH:
        permission = base_permission
    scopes = [s for s in scopes if s in ("group", "private", "*")] or base_scopes

    blocked = False
    blocked_reason = ""
    warning = ""

    if not enabled:
        blocked = True
        blocked_reason = "napcat_tools_enable=false"
    elif name in denied:
        blocked = True
        blocked_reason = "denied"
    elif allowed and name not in allowed:
        blocked = True
        blocked_reason = "not_in_whitelist"

    # 敏感工具放宽权限时仅提示警告，不直接屏蔽（前端展示 warning）
    if sensitivity in ("high", "critical") and permission in ("everyone", "member"):
        warning = f"{name} 为敏感工具，默认权限是 {base_permission}，当前被放宽为 {permission}"

    return {
        "name": name,
        "enabled": enabled and not blocked,
        "blocked": blocked,
        "blocked_reason": blocked_reason,
        "warning": warning,
        "permission": permission,
        "scopes": scopes,
        "base_permission": base_permission,
        "base_scopes": base_scopes,
        "sensitivity": sensitivity,
        "risk": risk,
    }


def is_tool_enabled(runtime, spec: dict) -> bool:
    """兼容旧接口：返回工具是否允许注入。"""
    policy = resolve_tool_policy(runtime, spec)
    return bool(policy["enabled"]) and not policy["blocked"]
