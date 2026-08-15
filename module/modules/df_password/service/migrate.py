"""旧配置迁移（group_list → command_response_groups）。"""

from app.modules.groups import migrate_group_list_config


def migrate_legacy_config(module) -> None:
    """旧配置迁移：group_list/group_list_mode → command_response_groups/cmd_group_mode（一次性）。"""
    migrate_group_list_config(
        module,
        legacy_key="group_list",
        legacy_mode_key="group_list_mode",
        new_key="command_response_groups",
        new_mode_key="cmd_group_mode",
    )
