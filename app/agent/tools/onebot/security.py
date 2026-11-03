"""OneBot 工具策略：总开关（「高级选项」）+ 逐工具策略（「工具管理 → OneBot 工具」）。

## 策略面

- ``onebot_tools_enable``：总开关，入口在「高级选项」页；
- ``onebot_tools_allowed`` / ``onebot_tools_denied``：允许 / 禁止名单（空=全部允许；
  禁止优先于允许）；
- ``onebot_tool_overrides``：逐工具权限/作用域覆盖：
  ``{"set_group_ban": {"permission": "group_owner", "scopes": ["group"]}}``；
- 其余（结果长度、调试日志、戳一戳冷却）已固化为框架常量，不再暴露。

**权限判定本身不变**：仍由 ``ToolSpec.allowed_for_event`` 按本轮调用者身份完成
（见 tools/spec.py）。本模块只负责「这个工具此刻是否注入 + 声明的权限档位」。
"""

from __future__ import annotations

PERMISSION_PATH = ("everyone", "member", "group_admin", "group_owner", "owner")

#: 敏感级 → 允许**授权该操作**的账号侧身份（「消息凭证」鉴权用；2026-11-03 重写）。
#:
#: - ``high``（群管理类工具）：bot 管理者或拥有者；
#: - ``critical``（自身账号修改类工具）：仅 bot 拥有者；
#: - ``normal``：不额外要求账号侧身份（沿用工具自身的 permission 判定）。
SENSITIVITY_AUTHORIZER_ROLES: dict[str, tuple[str, ...]] = {
    "high": ("manager", "owner"),
    "critical": ("owner",),
}


def authorizer_roles(sensitivity: str) -> tuple[str, ...]:
    """某敏感级需要的授权身份（大写/未知值按 normal 处理）。"""
    return SENSITIVITY_AUTHORIZER_ROLES.get(str(sensitivity or "").strip().lower(), ())


#: 配置键（Agent 配置）与默认值
TOOLS_ENABLED_KEY = "onebot_tools_enable"
DEFAULT_TOOLS_ENABLED = True


def _cfg(runtime, key: str, default):
    if runtime is None:
        return default
    try:
        return runtime.config.get(key, default)
    except Exception:  # noqa: BLE001 —— 配置读取失败按默认值处理
        return default


def tools_enabled(runtime) -> bool:
    """「高级选项 → 启用工具」总开关（读不到按默认值）。"""
    if runtime is None:
        return False
    return bool(_cfg(runtime, TOOLS_ENABLED_KEY, DEFAULT_TOOLS_ENABLED))


def _overrides(runtime) -> dict:
    raw = _cfg(runtime, "onebot_tool_overrides", {}) or {}
    return dict(raw) if isinstance(raw, dict) else {}


def _name_list(runtime, key: str) -> list[str]:
    raw = _cfg(runtime, key, []) or []
    if isinstance(raw, str):
        raw = raw.replace("，", ",").replace(",", "\n").splitlines()
    if not isinstance(raw, (list, tuple, set)):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def resolve_tool_policy(runtime, tool: dict) -> dict:
    """返回某个 OneBot 工具的最终策略（清单默认值 + 名单 + 逐工具覆盖）。"""
    name = str(tool.get("name", "") or "")
    base_permission = str(tool.get("permission", "member") or "member")
    base_scopes = list(tool.get("scopes", ["*"]) or ["*"])
    sensitivity = str(tool.get("sensitivity", "normal") or "normal")
    risk = str(tool.get("risk", "normal") or "normal")

    override = _overrides(runtime).get(name, {}) or {}
    permission = str(override.get("permission", base_permission) or base_permission)
    scopes = list(override.get("scopes", base_scopes) or base_scopes)
    if permission not in PERMISSION_PATH:
        permission = base_permission
    scopes = [s for s in scopes if s in ("group", "private", "*")] or base_scopes

    blocked = False
    blocked_reason = ""
    warning = ""

    if not tools_enabled(runtime):
        blocked = True
        blocked_reason = "onebot_tools_enable=false"
    elif name in _name_list(runtime, "onebot_tools_denied"):
        blocked = True
        blocked_reason = "denied"
    else:
        allowed = _name_list(runtime, "onebot_tools_allowed")
        if allowed and name not in allowed:
            blocked = True
            blocked_reason = "not_in_whitelist"

    # 敏感工具放宽权限时仅提示警告，不直接屏蔽（前端展示 warning）
    if sensitivity in ("high", "critical") and permission in ("everyone", "member"):
        warning = f"{name} 为敏感工具，默认权限是 {base_permission}，当前被放宽为 {permission}"

    return {
        "name": name,
        "enabled": tools_enabled(runtime),
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
