"""群组配置通用处理（跨模块共享）。

统一重复的群模式判断与启用群解析：
- check_group_enabled：单个群是否启用（all 默认启用 / partial 仅勾选 / none 禁用）；
- resolve_group_ids：获取排序后的启用群列表，新群自动注册进配置（写回）；
- migrate_group_list_config：旧版 list 群配置 → 新版 {gid: {enabled, index}} 一次性迁移。
"""

from __future__ import annotations

from app.modules.base import resolve_enabled_ids


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
    # 优先用登录缓存（gateway 登录时已拉取群列表），避免每次指令都实时拉取；
    # 缓存为空（未登录/测试 mock）时回退实时请求。
    cached = getattr(bot, "all_group_list", None) or []
    if cached:
        group_ids = [str(g) for g in cached]
    else:
        resp = await bot.get_group_list()
        if not resp or resp.get("status") != "ok":
            return []
        group_ids = [str(g.get("group_id", "")) for g in resp.get("data", []) or [] if g.get("group_id")]

    config = module.config
    group_configs = dict(config.get(key, {}) or {})

    # 自动注册新群
    dirty = False
    for gid in group_ids:
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


def migrate_group_list_config(
    module,
    legacy_key: str,
    legacy_mode_key: str,
    new_key: str,
    new_mode_key: str,
) -> bool:
    """旧版 list 群配置 → 新版 {gid: {enabled, index}} 一次性迁移（保留拖拽顺序）。

    幂等：新字段已有值或旧字段不存在时直接返回 False。
    """
    config = module.config
    if config.get(new_key):
        return False
    legacy = config.get(legacy_key, None)
    if not legacy:
        return False
    mode = config.get(legacy_mode_key, "all")
    groups = {}
    for i, gid in enumerate(resolve_enabled_ids(legacy, mode)):
        groups[gid] = {"enabled": True, "index": i}
    config.set(new_key, groups)
    if mode in ("partial", "none"):
        config.set(new_mode_key, mode)
    return True
