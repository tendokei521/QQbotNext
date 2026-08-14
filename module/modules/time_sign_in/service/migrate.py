"""旧配置迁移（priority_groups → group_configs）。"""

from app.modules.groups import migrate_group_list_config


def migrate_legacy_config(module) -> None:
    """旧配置迁移：priority_groups/priority_groups_mode → group_configs（一次性，保留拖拽顺序）。"""
    migrate_group_list_config(
        module,
        legacy_key="priority_groups",
        legacy_mode_key="priority_groups_mode",
        new_key="group_configs",
        new_mode_key="group_mode",
    )
