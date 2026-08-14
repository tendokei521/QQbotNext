"""权限范围校验（permission_scope）。"""


def _check_permission(module, event) -> bool:
    """按 permission_scope 配置检查事件权限（level 由框架计算）。"""
    scope = module.config.get("permission_scope", "bot_owner_only")
    level = event.authority_level
    if level is None:
        return False
    if scope == "everyone":
        return level >= 2
    if scope == "bot_owner_and_group_admin":
        return level >= 3
    return level >= 4  # bot_owner_only
