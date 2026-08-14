"""旧配置迁移（target → enable_forward_to_*）。"""


def migrate_legacy_config(module) -> None:
    """旧配置迁移：target 字段 → enable_forward_to_group / enable_forward_to_private（一次性）。

    映射：default → 均关；group → 仅群；private → 仅私聊；all → 均开（默认）。
    判断基于存储值（raw_config 含默认值，不能用于判断是否已迁移）。
    """
    config = module.config
    stored = config._service.get_module_config(module.module_name, module.bot_id) or {}
    target = stored.get("target")
    if target is None:
        return
    if "enable_forward_to_group" in stored or "enable_forward_to_private" in stored:
        return  # 已迁移
    if target == "default":
        config.set("enable_forward_to_group", False)
        config.set("enable_forward_to_private", False)
    elif target == "group":
        config.set("enable_forward_to_group", True)
        config.set("enable_forward_to_private", False)
    elif target == "private":
        config.set("enable_forward_to_group", False)
        config.set("enable_forward_to_private", True)
    # all / 其他 → 保持默认（均开）
