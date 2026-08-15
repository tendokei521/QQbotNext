"""语义化权限系统：角色 + 模块级过滤。

事件角色：
- ``member``：普通群成员
- ``group_admin``：群管理员
- ``group_owner``：群主
- ``owner``：Bot 拥有者

模块声明 ``permission``：
- ``everyone``：所有人
- ``member``：普通成员及以上
- ``group_admin``：群管理/群主
- ``group_owner``：群主
- ``owner``：仅 Bot 拥有者

黑白名单仍然作为前置 scope 过滤，先于角色判断。
"""

from __future__ import annotations

from app.domain.events import BaseEvent
from app.modules.base import BaseModule, ModulePermission

PERMISSIONS = ("everyone", "member", "group_admin", "group_owner", "owner")

ROLE_RANK = {
    "member": 0,
    "group_admin": 1,
    "group_owner": 2,
    "owner": 3,
}


def compute_event_permission(event: BaseEvent) -> str:
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

    owner_id = getattr(event, "owner_id", None)
    event.is_bot_owner = bool(event.user_id) and owner_id is not None and event.user_id == owner_id

    if event.is_bot_owner:
        event.permission_role = "owner"
    elif event.is_group_owner:
        event.permission_role = "group_owner"
    elif role == "group_admin":
        event.permission_role = "group_admin"
    else:
        event.permission_role = "member"

    return event.permission_role


def check_module_enabled(module: BaseModule) -> bool:
    """模块是否启用（读 authority.enabled）。"""
    auth = getattr(module, "authority", None)
    if auth is not None and hasattr(auth, "enabled"):
        return bool(auth.enabled)
    return True


def is_single_service_skipped(module: BaseModule, event: BaseEvent, config_service, gateway) -> bool:
    """单一服务模式：仅当同群有 ≥2 个在线 Bot 时，把响应权交给多群管理中指定的服务账号。"""
    try:
        webui = config_service.get_webui_config()
        single_service = webui.get("single_service", {}) or {}
        if not single_service.get(module.module_name):
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
        service_bot_index = group_config.get("service_bot_index")
        if service_bot_index is None:
            return False
        return event.bot_index != service_bot_index
    except Exception as e:
        from app.core.logger import logger

        logger.warning(f"[Auth] 单一服务判断异常: {e}")
        return False


def _scope_allows(event: BaseEvent, permission: ModulePermission) -> bool:
    """群/用户黑白名单前置过滤。"""
    group_id = str(getattr(getattr(event, "group", None), "group_id", "") or "")
    user_id = str(event.user_id or "")

    if group_id:
        mode, group_list = permission.group_mode, permission.group_list
        if mode == "whitelist":
            if not group_list or group_id not in group_list:
                return False
        elif mode == "blacklist" and group_id in group_list:
            return False

    if user_id:
        user_mode, user_list = permission.user_mode, permission.user_list
        if user_mode == "whitelist":
            if not (user_list and user_id in user_list):
                return False
        elif user_mode == "blacklist":
            if user_list and user_id in user_list:
                return False

    return True


def check_module_permission(module: BaseModule, event: BaseEvent) -> bool:
    """模块级权限过滤：先黑白名单，再按 permission 角色判断。"""
    auth = getattr(module, "authority", None)
    if auth is not None and hasattr(auth, "permission"):
        if not _scope_allows(event, auth.permission):
            return False

    policy = getattr(module, "permission", "member") or "member"

    # 私聊场景没有群角色概念：群管理/群主策略自动降级为 member，
    # 避免私聊被“仅群管理”错误拦截；owner 仍表示仅 Bot 拥有者。
    if getattr(event, "event_type", "") == "message_private":
        if policy in ("group_admin", "group_owner"):
            policy = "member"

    if policy == "everyone":
        return True

    required_rank = ROLE_RANK.get(policy, ROLE_RANK["member"])
    current_rank = ROLE_RANK.get(event.permission_role, ROLE_RANK["member"])
    return current_rank >= required_rank
