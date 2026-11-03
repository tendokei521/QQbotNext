"""OneBot 工具策略：**只有一个总开关** + 清单里的默认权限/作用域。

## 现在的策略面（2026-11-03 精简）

「工具扩展」插件并入框架后，OneBot 工具只剩一个开关注册在 Agent 配置里
（``onebot_tools_enable``，界面入口在「高级选项」页），不再有：

- 单工具的允许/禁止黑白名单；
- 逐工具的权限/作用域覆盖；
- 结果长度、调试日志、戳一戳冷却等调参（都固化为框架常量）。

每个工具的 ``permission`` / ``scopes`` / ``sensitivity`` 一律取清单默认值
（``manifest.json`` + ``manifest.py`` 的高危名单派生）。**权限判定本身不变**：
仍由 ``ToolSpec.allowed_for_event`` 按本轮调用者身份完成（见 tools/spec.py）。
"""

from __future__ import annotations

PERMISSION_PATH = ("everyone", "member", "group_admin", "group_owner", "owner")

#: 配置键（Agent 配置）与默认值
TOOLS_ENABLED_KEY = "onebot_tools_enable"
#: 合并前「工具扩展」模块的默认值是开；新账号保持同一行为
DEFAULT_TOOLS_ENABLED = True


def tools_enabled(runtime) -> bool:
    """「高级选项 → 启用工具」总开关（读不到按默认值）。"""
    if runtime is None:
        return False
    try:
        return bool(runtime.config.get(TOOLS_ENABLED_KEY, DEFAULT_TOOLS_ENABLED))
    except Exception:  # noqa: BLE001 —— 配置读取失败按默认值处理
        return DEFAULT_TOOLS_ENABLED


def resolve_tool_policy(runtime, tool: dict) -> dict:
    """返回某个 OneBot 工具的最终策略（清单默认值 + 总开关）。"""
    name = str(tool.get("name", "") or "")
    permission = str(tool.get("permission", "member") or "member")
    if permission not in PERMISSION_PATH:
        permission = "member"
    base_scopes = list(tool.get("scopes", ["*"]) or ["*"])
    scopes = [s for s in base_scopes if s in ("group", "private", "*")] or ["*"]
    return {
        "name": name,
        "enabled": tools_enabled(runtime),
        "blocked": False,
        "blocked_reason": "",
        "permission": permission,
        "scopes": scopes,
        "sensitivity": str(tool.get("sensitivity", "normal") or "normal"),
        "risk": str(tool.get("risk", "normal") or "normal"),
    }


def is_tool_enabled(runtime, spec: dict) -> bool:
    """兼容旧接口：工具是否允许注入（只看总开关）。"""
    return bool(resolve_tool_policy(runtime, spec)["enabled"])
