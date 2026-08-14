"""权限范围校验（permission_scope）。"""


def _check_permission(module, event) -> bool:
    """按 permission_scope 配置检查事件权限（level 由框架计算）。

    bot_owner_only 语义：框架的 level 4 只表示「群主」（role=owner），
    并不等于 Bot 拥有者；必须同时校验 event.user_id 与 event.owner_id 一致。
    """
    scope = module.config.get("permission_scope", "bot_owner_only")
    level = event.authority_level
    if level is None:
        return False
    if scope == "everyone":
        return level >= 2
    if scope == "bot_owner_and_group_admin":
        return level >= 3
    # bot_owner_only
    if level >= 5:
        return True  # 系统级权限（外部显式赋值）
    return level >= 4 and bool(event.user_id) and event.user_id == event.owner_id
