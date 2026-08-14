"""旧配置迁移（priority_groups → group_configs）。"""

from app.modules import resolve_enabled_ids


def migrate_legacy_config(module) -> None:
    """旧配置迁移：priority_groups/priority_groups_mode → group_configs（一次性，保留拖拽顺序）。"""
    config = module.config
    if config.get("group_configs"):
        return
    legacy = config.get("priority_groups", None)
    if not legacy:
        return
    mode = config.get("priority_groups_mode", "all")
    groups = {}
    for i, gid in enumerate(resolve_enabled_ids(legacy, mode)):
        groups[gid] = {"enabled": True, "index": i}
    config.set("group_configs", groups)
    if mode in ("partial", "none"):
        config.set("group_mode", mode)
