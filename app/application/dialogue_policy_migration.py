"""把旧的账号级权限配置迁移成群级覆盖（一次性，幂等）。

旧模型（全部账号级）：``group_enable`` / ``private_enable`` / ``group_mode`` +
``group_list`` / ``user_mode`` + ``user_list``。
新模型：``channel_overrides`` 的账号层 / 场景层（``__group__`` / ``__private__``）/ 群层。

放在 application 层（而不是基础设施的存储 mixin 里）：迁移需要读 Agent 配置，
那是上层语义；基础设施不该反向依赖 ``app.agent``。
"""

from __future__ import annotations

from typing import Any

from app.core.logger import logger
from app.domain.dialogue_policy import (
    SCENE_GROUP,
    SCENE_PRIVATE,
    trigger_mode_from,
    trigger_probability_of,
)

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


#: 触发方式迁到「回复与权限」的独立标记（与上面的场景/名单迁移分开：
#: 上面的迁移早已跑完（标记已写），不能靠它补触发方式。）
TRIGGER_MIGRATION_FLAG = "trigger_mode_migrated"


async def migrate_trigger_overrides(config_service: Any) -> int:
    """把 Agent 配置里的触发键落到账号级覆盖（幂等，返回写入账号数）。

    为什么需要：改造后触发方式的唯一编辑器是「回复与权限」；旧账号的
    ``trigger_at`` / ``trigger_keyword`` 留在 Agent 配置里。启动时把它们**复制**成
    账号级覆盖，保证"升级后行为一个字节都不变"，同时面板上立刻能看见当前值。
    """
    if await config_service.get_kv(TRIGGER_MIGRATION_FLAG, "") == "1":
        return 0
    bots = config_service.get_bots() or []
    if not bots:
        return 0

    migrated = 0
    for bot in bots:
        bot_id = str(bot.get("id") or bot.get("account_id") or "")
        if not bot_id:
            continue
        cfg = config_service.get_module_config(AGENT_CONFIG_MODULE, bot_id) or {}
        if not isinstance(cfg, dict) or not cfg:
            continue
        has_legacy = "trigger_at" in cfg or "trigger_keyword" in cfg
        if not has_legacy and "trigger_mode" not in cfg:
            continue

        mode = trigger_mode_from(cfg)
        config = {
            "trigger_mode": mode,
            "trigger_probability": trigger_probability_of(cfg),
            # 双写旧键：旧前端/旧脚本读到的语义不变
            "trigger_at": mode in ("at", "both", "all"),
            "trigger_keyword": [
                str(item).strip() for item in (cfg.get("trigger_keyword") or []) if str(item).strip()
            ],
        }
        await config_service.save_channel_override(bot_id, "agent", "", config=config)
        migrated += 1

    await config_service.set_kv(TRIGGER_MIGRATION_FLAG, "1")
    if migrated:
        logger.info(f"[Config] 已把触发方式迁移到账号级覆盖：{migrated} 个账号")
    return migrated
