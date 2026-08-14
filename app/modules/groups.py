"""群组配置通用处理（跨模块共享）。

统一三处重复的群模式判断与启用群解析：
- check_group_enabled：单个群是否启用（all 默认启用 / partial 仅勾选 / none 禁用）；
- resolve_group_ids：获取排序后的启用群列表，新群自动注册进配置（写回）。
"""

from __future__ import annotations


def check_group_enabled(
    config,
    group_id: str,
    key: str = "group_configs",
    mode_key: str = "group_mode",
) -> bool:
    """判断单个群是否启用。

    Args:
        config: ModuleConfig 门面或 dict（含 get 即可）
        group_id: 群号（str）
        key: 群配置字段名（存 {gid: {enabled, index}}）
        mode_key: 模式字段名（all / partial / none）
    """
    mode = config.get(mode_key, "all")
    group_configs = config.get(key, {}) or {}
    if mode == "all":
        return group_configs.get(group_id, {}).get("enabled", True)
    if mode == "none":
        return False
    return group_configs.get(group_id, {}).get("enabled", False)


async def resolve_group_ids(
    module,
    key: str = "group_configs",
    mode_key: str = "group_mode",
) -> list:
    """获取排序后的启用群 ID 列表（新群自动注册进配置）。

    - 自动注册：Bot 群列表中未配置的群补入 {enabled: True, index: 递增}；
    - 排序：按 index（WebUI 拖拽顺序）；
    - 需要 module.ctx.bot 提供群列表（无 bot 时返回空）。
    """
    bot = module.ctx.bot
    if bot is None:
        return []
    resp = await bot.get_group_list()
    if not resp or resp.get("status") != "ok":
        return []
    data = resp.get("data", []) or []

    config = module.config
    group_configs = dict(config.get(key, {}) or {})

    # 自动注册新群
    dirty = False
    for g in data:
        gid = str(g.get("group_id", ""))
        if not gid:
            continue
        if gid not in group_configs:
            group_configs[gid] = {"enabled": True, "index": len(group_configs)}
            dirty = True
    if dirty:
        config.set(key, group_configs)

    mode = config.get(mode_key, "all")
    if mode == "all":
        enabled = [gid for gid, cfg in group_configs.items() if cfg.get("enabled", True)]
    elif mode == "none":
        enabled = []
    else:
        enabled = [gid for gid, cfg in group_configs.items() if cfg.get("enabled", False)]

    enabled.sort(key=lambda gid: group_configs.get(gid, {}).get("index", 9999))
    return enabled
