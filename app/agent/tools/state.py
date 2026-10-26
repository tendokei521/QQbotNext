"""工具状态的**唯一判定点**：一个工具现在到底能不能用，为什么。

## 为什么需要它

此前"能不能用"被 5 个门卫、4 本登记本分别判断，任何一个判断错都无人兜底：

| 位置 | 判定依据 | 存储抽屉 |
|---|---|---|
| `OneBotToolSource.collect` | `onebot_tools_enable` | 模块 |
| `resolve_tool_policy` | 同上 + 黑白名单 | 模块（回退 agent） |
| `ModuleToolRegistry.enabled_specs` | `authority.enabled` + `tools_enabled` | 模块 |
| `tool_source` 过滤循环 | `module/system/mcp_tools_enabled` | agent |
| 执行期 | 只查权限等级 | —— |

于是出现过：「界面关掉了 → 写进 agent 抽屉 → 门卫翻模块抽屉 → 照样能调」。

## 现在的模型

```
一个管理员（本模块）+ 一张总表（tools_disabled）+ 固定判定链（短路）
```

判定链顺序固定，每一层只回答一件事：

1. **模块在不在** —— 工具所属模块未加载/未启用 → `module_disabled`
2. **模块开关** —— 模块自己的策略（OneBot 黑白名单、模块 `tools_enabled`）→ `module_switch_off`
3. **用户偏好** —— 统一总表 `tools_disabled` → `user_disabled`
4. **依赖** —— MCP 连接、API Key → `dependency_missing`
5. **会话作用域/权限** —— → `out_of_scope` / `permission_denied`

所有注入点（含 OneBot）只调 `resolve_tool_state`，不再各写一套 if。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.logger import logger

# ==================== 原因码 ====================

REASON_OK = "ok"
REASON_MODULE_DISABLED = "module_disabled"
REASON_MODULE_SWITCH_OFF = "module_switch_off"
REASON_USER_DISABLED = "user_disabled"
REASON_DEPENDENCY_MISSING = "dependency_missing"
REASON_OUT_OF_SCOPE = "out_of_scope"
REASON_PERMISSION_DENIED = "permission_denied"

#: 原因码 → 给用户看的一句话
REASON_TEXT = {
    REASON_OK: "可用",
    REASON_MODULE_DISABLED: "所属模块未启用",
    REASON_MODULE_SWITCH_OFF: "模块内的工具总开关已关闭",
    REASON_USER_DISABLED: "已在工具清单中关闭",
    REASON_DEPENDENCY_MISSING: "依赖未就绪（连接或密钥）",
    REASON_OUT_OF_SCOPE: "当前会话类型不在适用范围",
    REASON_PERMISSION_DENIED: "权限不足",
}

#: 用户级总表（agent 配置里**唯一**的 per-tool 开关）
USER_TOOLS_DISABLED_KEY = "tools_disabled"

#: 旧的三套 map：只读兼容，写入统一走 `tools_disabled`
LEGACY_USER_MAPS = ("module_tools_enabled", "system_tools_enabled", "mcp_tools_enabled")


@dataclass(frozen=True)
class ToolState:
    """一个工具的最终状态。"""

    name: str
    source: str
    owner: str
    available: bool
    reason: str = REASON_OK
    detail: str = ""
    #: 该工具的模块侧元信息（权限/作用域），供诊断展示
    extra: dict = field(default_factory=dict)

    @property
    def reason_text(self) -> str:
        return self.detail or REASON_TEXT.get(self.reason, self.reason)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "source": self.source,
            "owner": self.owner,
            "available": bool(self.available),
            "reason": self.reason,
            "reason_text": self.reason_text,
            "detail": self.detail,
        }


# ==================== 读取辅助 ====================


def _cfg_get(runtime: Any, key: str, default: Any = None) -> Any:
    config = getattr(runtime, "config", None)
    getter = getattr(config, "get", None)
    if not callable(getter):
        return default
    try:
        return getter(key, default)
    except Exception:  # noqa: BLE001 —— 配置读失败按默认走
        return default


def user_disabled_names(runtime: Any) -> set[str]:
    """用户级"已关闭"名单：新总表 + 三套旧 map 的只读兼容。"""
    names: set[str] = set()
    raw = _cfg_get(runtime, USER_TOOLS_DISABLED_KEY, [])
    if isinstance(raw, (list, tuple, set)):
        names.update(str(x) for x in raw if str(x or "").strip())
    for key in LEGACY_USER_MAPS:
        legacy = _cfg_get(runtime, key, {})
        if isinstance(legacy, dict):
            names.update(str(k) for k, v in legacy.items() if v is False)
    return names


def tools_module_loaded(runtime: Any) -> bool:
    """`llm_tools_extra`（OneBot/MCP 工具的拥有者）是否已加载。

    模块卸载时会把 `runtime.tools_options` 置 None。**这个判断必须挡在
    OneBot 策略读取之前** —— 否则模块关掉后 `policy_config` 会回退到
    agent 配置里的旧值，工具就此"复活"（历史 bug）。
    """
    return getattr(runtime, "tools_options", None) is not None


def _onebot_manifest_entry(name: str) -> dict | None:
    try:
        from app.plugins.llm_tools_extra.onebot.manifest import ONEBOT_TOOLS
    except Exception:  # noqa: BLE001 —— 模块未安装
        return None
    for tool in ONEBOT_TOOLS or []:
        if str(tool.get("name", "")) == name:
            return tool
    return None


# ==================== 判定链 ====================


def resolve_tool_state(
    runtime: Any,
    spec: Any = None,
    *,
    name: str = "",
    source: str = "",
    session_scope: str | None = None,
    event: Any = None,
) -> ToolState:
    """判定一个工具是否可用。`spec` 与 `(name, source)` 二选一。"""
    tool_name = str(name or getattr(spec, "name", "") or "")
    tool_source = str(source or getattr(spec, "source", "") or "")
    owner = ""
    if spec is not None:
        module = getattr(spec, "module", None)
        owner = getattr(module, "module_name", "") or ""
        if not owner and tool_source == "onebot":
            owner = "llm_tools_extra"
    if not owner and tool_source == "onebot":
        owner = "llm_tools_extra"

    def _state(ok: bool, reason: str, detail: str = "", extra: dict | None = None) -> ToolState:
        return ToolState(
            name=tool_name,
            source=tool_source,
            owner=owner or "framework",
            available=ok,
            reason=reason,
            detail=detail,
            extra=extra or {},
        )

    # ── ① 所属模块在不在 ──
    if tool_source == "onebot":
        if not tools_module_loaded(runtime):
            return _state(False, REASON_MODULE_DISABLED, "llm_tools_extra 模块未启用")
    elif tool_source in ("module", "mcp"):
        module = getattr(spec, "module", None) if spec is not None else None
        authority = getattr(module, "authority", None)
        if authority is not None and not getattr(authority, "enabled", True):
            return _state(False, REASON_MODULE_DISABLED, f"{owner} 模块未启用")

    # ── ② 模块自己的策略 ──
    extra: dict = {}
    if tool_source == "onebot":
        entry = _onebot_manifest_entry(tool_name)
        if entry is None:
            return _state(False, REASON_MODULE_SWITCH_OFF, "工具已不在清单中")
        try:
            from app.plugins.llm_tools_extra.onebot.security import resolve_tool_policy

            policy = resolve_tool_policy(runtime, entry)
        except Exception as e:  # noqa: BLE001 —— 策略算不出来按不可用处理（安全侧）
            return _state(False, REASON_MODULE_SWITCH_OFF, f"策略解析失败: {e}")
        extra = {
            "permission": policy.get("permission"),
            "scopes": list(policy.get("scopes") or []),
            "sensitivity": policy.get("sensitivity"),
        }
        if not policy.get("enabled") or policy.get("blocked"):
            reason = policy.get("blocked_reason") or "disabled"
            detail = (
                "OneBot 工具总开关已关闭"
                if reason == "onebot_tools_enable=false"
                else ("已在黑名单中" if reason == "denied" else "不在白名单中")
            )
            return _state(False, REASON_MODULE_SWITCH_OFF, detail, extra)
        scopes = extra["scopes"]
        if session_scope and scopes and "*" not in scopes and session_scope not in scopes:
            return _state(False, REASON_OUT_OF_SCOPE, f"仅适用于 {scopes}", extra)
    elif tool_source == "module":
        module = getattr(spec, "module", None) if spec is not None else None
        module_cfg = getattr(module, "config", None)
        enabled_map = module_cfg.get("tools_enabled", {}) if module_cfg is not None else {}
        if isinstance(enabled_map, dict) and enabled_map.get(tool_name) is False:
            return _state(False, REASON_MODULE_SWITCH_OFF, f"{owner} 里已关闭该工具")

    # ── ③ 用户级总表 ──
    if tool_name in user_disabled_names(runtime):
        return _state(False, REASON_USER_DISABLED, "已在工具清单中关闭", extra)

    # ── ④ 依赖 ──
    if tool_source == "mcp":
        manager = getattr(runtime, "mcp_service", None)
        if manager is None or not getattr(manager, "enabled", lambda: True)():
            return _state(False, REASON_DEPENDENCY_MISSING, "MCP 未启用", extra)
    elif tool_source == "system":
        try:
            from app.agent.tools.policy import prereq_state

            ready, why = prereq_state(runtime, tool_name)
        except Exception:  # noqa: BLE001
            ready, why = True, ""
        if not ready:
            return _state(False, REASON_DEPENDENCY_MISSING, why or "前置功能未开启", extra)

    # ── ⑤ 执行期权限（如给了事件）──
    if event is not None and spec is not None:
        try:
            scope = session_scope or ("private" if getattr(event, "message_type", "") == "private" else "group")
            if not spec.allowed_for_event(event, scope=scope):
                return _state(False, REASON_PERMISSION_DENIED, "当前身份权限不足", extra)
        except Exception as e:  # noqa: BLE001 —— 判定不了就放行，交给执行期再判
            logger.debug(f"[Tool] 权限预判失败（{tool_name}），交由执行期判定: {e}")

    return _state(True, REASON_OK, "", extra)


def resolve_all_tool_states(runtime: Any, *, include_unavailable: bool = True) -> list[ToolState]:
    """枚举**所有来源**的工具及其状态（统一页面/诊断用）。"""
    states: list[ToolState] = []
    seen: set[str] = set()

    # 模块工具（含已被用户关闭的：注册表里都在）
    registry = getattr(runtime, "llm_tools", None)
    for spec in (registry.all_specs() if registry is not None else []):
        st = resolve_tool_state(runtime, spec)
        states.append(st)
        seen.add(st.name)

    # 系统工具
    try:
        from app.agent.tools.session_tools import build_session_tools

        for spec in build_session_tools(runtime, None) or []:
            if spec.name in seen:
                continue
            states.append(resolve_tool_state(runtime, spec))
            seen.add(spec.name)

        # P5：spill 取回工具也是系统工具（漏登记 = 界面上看不到）
        from app.agent.tools.history_search import build_history_search_tool
        from app.agent.tools.recall import build_recall_tool

        search = build_history_search_tool(runtime, None)
        if search.name not in seen:
            states.append(resolve_tool_state(runtime, search))
            seen.add(search.name)
        recall = build_recall_tool(runtime, None)
        if recall.name not in seen:
            states.append(resolve_tool_state(runtime, recall))
            seen.add(recall.name)
    except Exception as e:  # noqa: BLE001 —— 组装失败不该拖垮清单
        logger.debug(f"[Tool] 系统工具清单组装失败（已忽略）: {e}")

    # OneBot 工具
    try:
        from app.plugins.llm_tools_extra.onebot.manifest import ONEBOT_TOOLS
    except Exception:  # noqa: BLE001
        ONEBOT_TOOLS = []
    for tool in ONEBOT_TOOLS or []:
        name = str(tool.get("name", "") or "")
        if not name or name in seen:
            continue
        st = resolve_tool_state(
            runtime,
            name=name,
            source="onebot",
            session_scope=None,
        )
        st = ToolState(
            name=st.name,
            source=st.source,
            owner=st.owner,
            available=st.available,
            reason=st.reason,
            detail=st.detail,
            extra={
                **st.extra,
                "description": str(tool.get("description", "") or ""),
                "category": str(tool.get("category", "onebot") or "onebot"),
            },
        )
        states.append(st)
        seen.add(name)

    # MCP 工具
    manager = getattr(runtime, "mcp_service", None)
    if manager is not None:
        try:
            for spec in manager.build_tools() or []:
                if spec.name in seen:
                    continue
                states.append(resolve_tool_state(runtime, spec))
                seen.add(spec.name)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[Tool] MCP 工具枚举失败（已忽略）: {e}")

    if include_unavailable:
        return states
    return [s for s in states if s.available]


def set_tool_disabled(runtime: Any, name: str, disabled: bool) -> None:
    """在**唯一**用户总表里记录开关（不再散写三套 map）。"""
    config = getattr(runtime, "config", None)
    if config is None:
        return
    current = _cfg_get(runtime, USER_TOOLS_DISABLED_KEY, [])
    names = [str(x) for x in current] if isinstance(current, (list, tuple, set)) else []
    if disabled:
        if name not in names:
            names.append(name)
    else:
        names = [n for n in names if n != name]
    config.set(USER_TOOLS_DISABLED_KEY, names, auto_save=False)
    save = getattr(config, "save", None)
    if callable(save):
        save()


__all__ = [
    "LEGACY_USER_MAPS",
    "REASON_DEPENDENCY_MISSING",
    "REASON_MODULE_DISABLED",
    "REASON_MODULE_SWITCH_OFF",
    "REASON_OK",
    "REASON_OUT_OF_SCOPE",
    "REASON_PERMISSION_DENIED",
    "REASON_TEXT",
    "REASON_USER_DISABLED",
    "USER_TOOLS_DISABLED_KEY",
    "ToolState",
    "resolve_all_tool_states",
    "resolve_tool_state",
    "set_tool_disabled",
    "tools_module_loaded",
    "user_disabled_names",
]
