"""把旧的账号级权限配置迁移成群级覆盖（一次性，幂等）。

旧模型（全部账号级）：``group_enable`` / ``private_enable`` / ``group_mode`` +
``group_list`` / ``user_mode`` + ``user_list``。
新模型：``channel_overrides`` 的账号层 / 场景层（``__group__`` / ``__private__``）/ 群层。

放在 application 层（而不是基础设施的存储 mixin 里）：迁移需要读 Agent 配置，
那是上层语义；基础设施不该反向依赖 ``app.agent``。
"""

from __future__ import annotations

from typing import Any

from app.domain.dialogue_policy import SCENE_GROUP, SCENE_PRIVATE
from app.core.logger import logger

#: Agent 配置在配置中心里的模块名（与 app.agent.config.AGENT_CONFIG_MODULE 一致；
#: 这里写字面量是为了避免 application → agent 的反向依赖）。
AGENT_CONFIG_MODULE = "agent"

MIGRATION_FLAG = "channel_overrides_migrated"


async def migrate_legacy_dialog_policies(config_service: Any) -> bool:
    """执行迁移；返回 True = 本次真的写入了迁移标记。

    - 没有任何账号时不写标记（新装用户之后建号，下次启动还能迁）；
    - 已经有标记直接跳过（幂等）。
    """
    if await config_service.get_kv(MIGRATION_FLAG, "") == "1":
        return False
    bots = config_service.get_bots() or []
    if not bots:
        return False

    migrated = 0
    for bot in bots:
        bot_id = str(bot.get("id") or bot.get("account_id") or "")
        if not bot_id:
            continue
        cfg = config_service.get_module_config(AGENT_CONFIG_MODULE, bot_id) or {}
        if not isinstance(cfg, dict) or not cfg:
            continue

        group_enable = bool(cfg.get("group_enable", False))
        private_enable = bool(cfg.get("private_enable", True))
        group_mode = str(cfg.get("group_mode") or "blacklist")
        group_list = [str(item).strip() for item in (cfg.get("group_list") or []) if str(item).strip()]
        user_mode = str(cfg.get("user_mode") or "blacklist")
        user_list = [str(item).strip() for item in (cfg.get("user_list") or []) if str(item).strip()]

        if user_list and user_mode in ("blacklist", "whitelist"):
            await config_service.save_channel_override(
                bot_id, "agent", "", permission={"user_mode": user_mode, "user_list": user_list}
            )
            migrated += 1

        if group_mode == "whitelist" and group_list:
            # 白名单：群聊默认关，只有列出的群开（与旧语义一致）
            await config_service.save_channel_override(bot_id, "agent", SCENE_GROUP, enabled=False)
            for group_id in group_list:
                await config_service.save_channel_override(bot_id, "agent", group_id, enabled=True)
            migrated += 1 + len(group_list)
        else:
            await config_service.save_channel_override(bot_id, "agent", SCENE_GROUP, enabled=group_enable)
            migrated += 1
            if group_mode == "blacklist" and group_enable:
                for group_id in group_list:
                    await config_service.save_channel_override(bot_id, "agent", group_id, enabled=False)
                    migrated += 1

        await config_service.save_channel_override(bot_id, "agent", SCENE_PRIVATE, enabled=private_enable)
        migrated += 1

    await config_service.set_kv(MIGRATION_FLAG, "1")
    if migrated:
        logger.info(f"[Config] 已迁移账号级场景/名单到群级覆盖：{migrated} 行")
    return True
