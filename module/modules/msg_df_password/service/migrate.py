"""旧配置迁移（group_list → command_response_groups）。"""

from app.modules import resolve_enabled_ids


def migrate_legacy_config(module) -> None:
    """旧配置迁移：group_list/group_list_mode → command_response_groups/cmd_group_mode（一次性）。"""
    config = module.config
    if config.get("command_response_groups"):
        return
    legacy = config.get("group_list", None)
    if not legacy:
        return
    mode = config.get("group_list_mode", "all")
    groups = {}
    for i, gid in enumerate(resolve_enabled_ids(legacy, mode)):
        groups[gid] = {"enabled": True, "index": i}
    config.set("command_response_groups", groups)
    if mode in ("partial", "none"):
        config.set("cmd_group_mode", mode)
