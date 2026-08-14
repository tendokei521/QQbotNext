"""权限系统：事件权限计算 + 模块启用检查。

自原 basic/check_permission.py 移植，逻辑保持一致：
- 群/用户黑白名单 → 0 级拦截；
- 群主/管理员提权 → 4 / 3 级；
- 模块 AUTHORITY_TYPE 决定可接受的最低/最高等级；
- refuse 拒绝所有普通事件（仅用于声明「不响应消息」的系统级模块）。
"""

from __future__ import annotations


from app.core.logger import logger
from app.domain.events import BaseEvent
from app.modules.base import BaseModule, ModulePermission

# 权限类型定义（5 级只能通过外部赋值，不会从消息自动提取）
AUTHORITY_TYPES: dict[str, dict] = {
    "all": {"min_level": 0, "max_level": 5, "description": "允许所有用户"},
    "normal": {"min_level": 2, "max_level": 5, "description": "允许普通用户及以上"},
    "strict": {"min_level": 3, "max_level": 5, "description": "只允许管理员及以上"},
    "admin": {"min_level": 4, "max_level": 5, "description": "只允许 Bot 拥有者及以上"},
    "refuse": {"min_level": -1, "max_level": -1, "description": "拒绝所有非系统事件"},
}


def check_module_enabled(module: BaseModule) -> bool:
    """模块是否启用（读 authority.enabled）。"""
    auth = getattr(module, "authority", None)
    if auth is not None and hasattr(auth, "enabled"):
        return bool(auth.enabled)
    return True


def is_single_service_skipped(module: BaseModule, event: BaseEvent, config_service, gateway) -> bool:
    """单一服务模式：仅当同群有 ≥2 个在线 Bot 时，把响应权交给多群管理中指定的服务账号。

    设计初衷：多账号在同一群聊时只有一个账号响应，服务账号由多群管理按群配置；
    单账号群 / 未指定服务账号的群不触发本规则，各账号照常响应。

    - 模块未启用单一服务 / 非群事件 → 不跳过；
    - 群内在线 Bot 数 < 2 → 不触发规则（单账号群照常响应）；
    - 群内 ≥2 个 Bot 且未配置服务账号 → 不限制（所有账号均可响应）；
    - 群内 ≥2 个 Bot 且配置了服务账号 → 仅服务账号响应，其余跳过。
    """
    try:
        webui = config_service.get_webui_config()
        single_service = webui.get("single_service", {}) or {}
        if not single_service.get(module.module_name):
            return False
        group_id = getattr(getattr(event, "group", None), "group_id", None)
        if not group_id:
            return False

        # 核心触发条件：仅当 ≥2 个在线 Bot 当前在该群时才应用规则
        online_in_group = 0
        for conn in gateway.connections.values():
            if conn.status == "connected" and group_id in (conn.all_group_list or []):
                online_in_group += 1
        if online_in_group < 2:
            return False

        # 多账号群：按多群管理中该群配置的服务账号决定响应权
        multi_group = webui.get("multi_group", {}) or {}
        group_config = (multi_group.get("groups", {}) or {}).get(str(group_id)) or {}
        service_bot_index = group_config.get("service_bot_index")
        if service_bot_index is None:
            return False  # 未指定服务账号 → 不限制
        return event.bot_index != service_bot_index
    except Exception as e:
        logger.warning(f"[Auth] 单一服务判断异常: {e}")
        return False


def check_permission(event: BaseEvent, permission: ModulePermission,
                     group_id: str = "", user_id: str = "") -> int:
    """核心权限判定，返回等级：-1 / 0 / 2 / 3 / 4。"""
    # 1. 群权限
    if group_id:
        mode, group_list = permission.group_mode, permission.group_list
        if mode == "whitelist":
            if not group_list or group_id not in group_list:
                return 0
        elif mode == "blacklist" and group_id in group_list:
            return 0

    # 群事件无需用户校验
    if not user_id:
        return 2

    # 2. 用户权限
    user_mode, user_list = permission.user_mode, permission.user_list
    if user_mode == "whitelist":
        if not (user_list and user_id in user_list):
            return 0
    elif user_mode == "blacklist":
        if user_list and user_id in user_list:
            return 0

    # 3. 角色提权（最高 4）
    return _calculate_authority_level(event, user_id)


def _calculate_authority_level(event: BaseEvent, user_id: str) -> int:
    role = ""
    if hasattr(event, "user") and event.user is not None:
        role = getattr(event.user, "role", "") or ""
    if role == "owner":
        return 4
    if role == "admin":
        return 3
    return 2


def get_authority_type(module: BaseModule) -> str:
    return str(getattr(module, "authority_type", "normal") or "normal").lower()


def check_event_permission(event: BaseEvent, module: BaseModule) -> bool:
    """计算事件权限并写回 event.authority_level / authority_check。"""
    authority_type = get_authority_type(module)
    type_config = AUTHORITY_TYPES.get(authority_type, AUTHORITY_TYPES["normal"])

    if event.authority_level is not None:
        current_level = event.authority_level
    else:
        current_level = _calculate_event_level(event, module)

    event.authority_level = current_level
    min_level, max_level = type_config["min_level"], type_config["max_level"]

    if authority_type == "refuse":
        event.authority_check = False
        if current_level not in (-1, 5):
            logger.debug(f"[Auth]模块 {module.sign} 为 refuse 模式，拒绝事件")
            return False
        event.authority_check = True
        return True

    ok = min_level <= current_level <= max_level
    event.authority_check = ok

    if authority_type in ("normal", "strict", "admin") and current_level >= 2:
        event.authority_enabled = True
    elif authority_type == "all":
        event.authority_enabled = True
    else:
        event.authority_enabled = False

    return event.authority_enabled


def _calculate_event_level(event: BaseEvent, module: BaseModule) -> int:
    group_id = ""
    user_id = ""
    if hasattr(event, "group") and event.group is not None:
        group_id = str(getattr(event.group, "group_id", "") or "")
    if event.user_id:
        user_id = str(event.user_id)
    permission = _permission_of(module)
    return check_permission(event, permission, group_id, user_id)


def _permission_of(module: BaseModule) -> ModulePermission:
    auth = getattr(module, "authority", None)
    if auth is not None and hasattr(auth, "permission"):
        return auth.permission
    return ModulePermission()


def set_system_authority(event: BaseEvent, level: int = 5) -> None:
    """手动赋予事件系统级权限（绕过所有 AUTHORITY_TYPE 限制）。"""
    event.authority_level = level
    logger.debug(f"[Auth]事件权限已手动设置为 {level} 级")
