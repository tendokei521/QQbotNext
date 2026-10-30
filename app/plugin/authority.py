"""语义化权限系统：角色 + 模块级过滤。

事件角色（身份）：
- ``member``：普通群成员
- ``group_admin``：群管理员
- ``group_owner``：群主
- ``manager``：Agent 管理者（配置里的 ``agent_manager_list``）
- ``owner``：Bot 拥有者，或配置里的 ``agent_owner_list``

模块声明 ``permission``：
- ``everyone``：所有人
- ``member``：普通成员及以上
- ``group_admin``：群管理/群主
- ``group_owner``：群主
- ``manager``：管理者与拥有者
- ``owner``：仅拥有者

**为什么要有名册**：群角色是 QQ 群给的，管不到「谁有资格调教这个 Bot」。名册
（``agent_owner_list`` / ``agent_manager_list``）是 Agent 自己的身份，跨群生效：
有人在任何群里说句话，只要 QQ 号在拥有者名册里，他就是 owner 档。

黑白名单仍然作为前置 scope 过滤，先于角色判断。
"""

from __future__ import annotations

from collections.abc import Iterable

from app.domain.events import BaseEvent
from app.domain.response_access import (  # noqa: F401 - PERMISSIONS/ROLE_RANK 为历史导出
    PERMISSIONS,
    ROLE_RANK,
    enabled_of,
    normalize_tier,
    scope_reason,
    tier_allows,
)
from app.plugin.base import BaseModule, ModulePermission

#: 各档位的语义说明（展示用；判定在 domain.response_access.tier_allows）
PERMISSION_DOCS = {
    "everyone": "所有人",
    "member": "普通群成员及以上",
    "group_admin": "群管理员/群主",
    "group_owner": "群主",
    "manager": "管理者与拥有者",
    "owner": "仅拥有者",
}


def _as_id_set(value: object) -> set[str]:
    """把配置里的 QQ 号名册规整成字符串集合（容忍空值、数字、空白项）。"""
    if not value:
        return set()
    if isinstance(value, str):
        value = value.replace(",", "\n").splitlines()  # noqa: PLW2901 - 允许 "1,2,3" 写法
    if not isinstance(value, Iterable):
        return set()
    return {str(item).strip() for item in value if str(item).strip()}


def roster_lists(config: object) -> tuple[set[str], set[str]]:
    """从 Agent 配置里取出 (拥有者名册, 管理者名册)。

    ``config`` 既可能是 dict 也可能是带 ``.get`` 的配置对象；取不到就返回空集合，
    绝不因为配置缺字段而抛错（权限判断不该成为新的崩溃点）。
    """
    getter = getattr(config, "get", None)
    if not callable(getter):
        return set(), set()
    try:
        return (
            _as_id_set(getter("agent_owner_list", [])),
            _as_id_set(getter("agent_manager_list", [])),
        )
    except Exception:  # noqa: BLE001 - 配置结构异常时退化为「无名册」
        return set(), set()


def compute_event_permission(
    event: BaseEvent,
    *,
    owner_list: Iterable[object] | None = None,
    manager_list: Iterable[object] | None = None,
) -> str:
    """计算事件最终权限角色，并写回 event 上的语义化字段。"""
    role = "member"
    user_role = getattr(getattr(event, "user", None), "role", "") or ""
    if user_role == "owner":
        role = "group_owner"
    elif user_role == "admin":
        role = "group_admin"

    event.role = role
    event.is_group_owner = role == "group_owner"
    event.is_admin = role in ("group_admin", "group_owner")
    event.is_member = True

    # 名册身份（Agent 自己的 owner/manager）优先于群角色：群主不等于有资格改配置
    user_key = str(getattr(event, "user_id", "") or "")
    in_owner_roster = bool(user_key) and user_key in _as_id_set(owner_list)
    in_manager_roster = bool(user_key) and user_key in _as_id_set(manager_list)
    event.is_agent_owner = in_owner_roster
    event.is_agent_manager = in_manager_roster

    # ``is_bot_owner`` 曾来自「账号级 owner_id」，v3 起**拥有者身份只有名册一个来源**
    # （原 ``bots.owner_id`` 已废弃，迁移脚本把它并进 ``agent_owner_list``）。
    # 字段名保留以免第三方插件读崩，语义与 ``is_agent_owner`` 一致。
    event.is_bot_owner = in_owner_roster

    if event.is_bot_owner or in_owner_roster:
        event.permission_role = "owner"
    elif in_manager_roster:
        event.permission_role = "manager"
    elif event.is_group_owner:
        event.permission_role = "group_owner"
    elif role == "group_admin":
        event.permission_role = "group_admin"
    else:
        event.permission_role = "member"

    return event.permission_role


def check_module_enabled(module: BaseModule) -> bool:
    """模块是否启用（读 authority.enabled；语义见 ``domain.response_access.enabled_of``）。"""
    return enabled_of(module)


def single_service_key(module) -> str:
    """模块在 `webui.single_service` 配置里使用的键。

    缺省是 ``module.module_name``；虚拟模块（框架级 Agent）用 ``single_service_key``
    覆写成 WebUI 存的 ``"agent"``——否则 UI 开着、运行时不生效。
    """
    key = str(getattr(module, "single_service_key", "") or "")
    return key or str(getattr(module, "module_name", "") or "")


def declares_single_service(module) -> bool:
    """模块是否**声明**支持单一服务模式（``BaseModule.supports_single_service``）。

    单一服务是声明制能力：未声明者一律不参与该功能——WebUI 不给开关，配置写不进去，
    运行时也不会因为配置里残留的 true 而被跳过。判定只看类属性，不做任何配置推断。
    """
    return bool(getattr(module, "supports_single_service", False))


def is_single_service_skipped(module: BaseModule, event: BaseEvent, config_service, gateway) -> bool:
    """单一服务模式：仅当同群有 ≥2 个在线 Bot 时，把响应权交给多群管理中指定的服务账号。"""
    try:
        if not declares_single_service(module):
            return False  # 未声明：配置残留也不生效
        webui = config_service.get_webui_config()
        single_service = webui.get("single_service", {}) or {}
        if not single_service.get(single_service_key(module)):
            return False
        group_id = getattr(getattr(event, "group", None), "group_id", None)
        if not group_id:
            return False

        online_in_group = 0
        for conn in gateway.connections.values():
            if conn.status == "connected" and group_id in (conn.all_group_list or []):
                online_in_group += 1
        if online_in_group < 2:
            return False

        multi_group = webui.get("multi_group", {}) or {}
        group_config = (multi_group.get("groups", {}) or {}).get(str(group_id)) or {}
        # 单一服务指定的是"由哪个账号负责本群"，用的是账号 id（曾用数组下标，已废弃）
        service_account = group_config.get("service_bot_id")
        if service_account is None:
            return False
        return str(event.account_id) != str(service_account)
    except Exception as e:
        from app.core.logger import logger

        logger.warning(f"[Auth] 单一服务判断异常: {e}")
        return False


def _scope_allows(event: BaseEvent, permission: ModulePermission) -> bool:
    """群/用户黑白名单前置过滤（语义与 ``domain.response_access.scope_reason`` 同源）。"""
    group_id = str(getattr(getattr(event, "group", None), "group_id", "") or "")
    user_id = str(event.user_id or "")

    if group_id:
        mode, group_list = permission.group_mode, permission.group_list
        listed = group_id in {str(item).strip() for item in (group_list or [])}
        if scope_reason(str(mode or ""), listed, kind="group") is not None:
            return False

    if user_id:
        user_mode, user_list = permission.user_mode, permission.user_list
        listed = user_id in {str(item).strip() for item in (user_list or [])}
        if scope_reason(str(user_mode or ""), listed, kind="user") is not None:
            return False

    return True


def required_tier_of(module: object) -> str:
    """模块要求的身份档位（非法值按 ``member``，判定在 ``tier_allows``）。"""
    return normalize_tier(getattr(module, "permission", "member"), "member")


def check_module_permission(module: BaseModule, event: BaseEvent) -> bool:
    """模块级权限过滤：先黑白名单，再按 permission 角色判断。"""
    auth = getattr(module, "authority", None)
    if auth is not None and hasattr(auth, "permission"):
        if not _scope_allows(event, auth.permission):
            return False

    policy = required_tier_of(module)
    if policy == "everyone":
        return True
    return tier_allows(
        policy,
        getattr(event, "permission_role", "member"),
        is_private=getattr(event, "event_type", "") == "message_private",
    )
