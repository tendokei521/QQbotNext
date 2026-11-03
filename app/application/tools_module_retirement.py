"""`llm_tools_extra` 模块退役：把 OneBot 工具开关并入账号级 agent 配置。

2026-11-03：「工具扩展」插件并入框架，OneBot 工具只剩一个本体键
``onebot_tools_enable``（「高级选项」页）。旧值存在 bindings 的模块配置/授权里
（``capability_id='llm_tools_extra'``），所以启动时搬一次：

- 旧"模块启用"与旧"模块内 onebot 总开关"的**与** = 旧实际是否能用工具；
- 写进该账号 agent 配置的 ``onebot_tools_enable``；
- 删掉旧模块的 binding 行（模块已不存在），并写 kv 标记保证只跑一次。

为什么必须覆盖 agent 里已有的同名键：合并前该键归模块所有，agent 配置里的值只是
"整份默认值回写"的残渣（GET 还会把它滤掉），不代表用户选择。搬完后 agent 键才是真相。
"""

from __future__ import annotations

from typing import Any

from app.core.logger import logger

#: 已退役的模块 id
RETIRED_MODULE = "llm_tools_extra"

#: 迁移标记（写进 kv，值为 "1"）
MIGRATION_FLAG = "llm_tools_extra_retired_v1"

#: 新家的能力 id 与键
AGENT_CAPABILITY = "agent"
TARGET_KEY = "onebot_tools_enable"

#: 旧模块配置里需要原样搬走的逐工具策略键（OneBot 工具页继续编辑它们）
POLICY_KEYS: tuple[str, ...] = (
    "onebot_tools_allowed",
    "onebot_tools_denied",
    "onebot_tool_overrides",
)


def _bot_id_of(bot: dict) -> str:
    return str(bot.get("id") or bot.get("account_id") or "")


async def migrate_tools_module_config(config_service: Any) -> dict:
    """把「工具扩展」的旧开关与逐工具策略并入 agent 配置，然后删掉旧模块行。

    Returns:
        ``{"accounts": 处理了几个账号, "enabled": 其中开了工具的数量, "deleted": 删了几行旧绑定}``
    """
    report: dict[str, Any] = {"accounts": 0, "enabled": 0, "deleted": 0, "errors": []}
    try:
        if await config_service.get_kv(MIGRATION_FLAG, "") == "1":
            return report
    except Exception as e:  # noqa: BLE001 —— 读不到标记就按未迁移处理
        logger.warning(f"[Migrate] 工具扩展退役：读迁移标记失败（按未迁移处理）: {e}")

    bots = config_service.get_bots() or []
    for bot in bots:
        bot_id = _bot_id_of(bot)
        if not bot_id:
            continue
        try:
            legacy_cfg = config_service.get_module_config(RETIRED_MODULE, bot_id) or {}
            legacy_auth = config_service.get_module_authority(RETIRED_MODULE, bot_id) or {}
            # 旧实际能力 = 模块启用 且 模块内导出了 OneBot 工具（默认 True）
            legacy_enabled = bool(legacy_auth.get("enabled", True)) and bool(
                legacy_cfg.get(TARGET_KEY, True)
            )
            existing = config_service.get_binding(AGENT_CAPABILITY, "account", bot_id) or {}
            base = existing.get("config") if isinstance(existing.get("config"), dict) else {}
            policy = {key: legacy_cfg[key] for key in POLICY_KEYS if key in legacy_cfg}
            await config_service.save_binding(
                AGENT_CAPABILITY,
                "account",
                bot_id,
                enabled=bool(existing.get("enabled", True)),
                config={**base, TARGET_KEY: legacy_enabled, **policy},
            )
            report["accounts"] += 1
            report["enabled"] += int(legacy_enabled)
        except Exception as e:  # noqa: BLE001 —— 单账号失败不影响其它
            report["errors"].append(f"{bot_id}: {e}")
            logger.error(f"[Migrate] 工具扩展退役失败（账号 {bot_id}）: {e}")

    # 删掉旧模块的绑定行（模块已不存在，留着会让旧能力页/迁移逻辑再次看到）
    for row in config_service.list_bindings(RETIRED_MODULE) or []:
        scope_type = str(row.get("scope_type") or "")
        scope_id = str(row.get("scope_id") or "")
        if not scope_type:
            continue
        try:
            await config_service.db.execute(
                "DELETE FROM bindings WHERE capability_id=? AND scope_type=? AND scope_id=?",
                (RETIRED_MODULE, scope_type, scope_id),
            )
            report["deleted"] += 1
        except Exception as e:  # noqa: BLE001
            report["errors"].append(f"{scope_type}:{scope_id} {e}")
            logger.error(f"[Migrate] 工具扩展旧绑定删除失败（{scope_type}:{scope_id}）: {e}")

    reloader = getattr(config_service, "_load_bindings", None)
    if callable(reloader):
        await reloader()

    if not report["errors"]:
        await config_service.set_kv(MIGRATION_FLAG, "1")
        logger.info(
            f"[Migrate] 工具扩展模块已退役：{report['accounts']} 个账号（其中 "
            f"{report['enabled']} 个保持开启），删除 {report['deleted']} 行旧绑定"
        )
    else:
        logger.error(f"[Migrate] 工具扩展退役有 {len(report['errors'])} 个错误（下次启动重试）")
    return report


__all__ = [
    "AGENT_CAPABILITY",
    "MIGRATION_FLAG",
    "RETIRED_MODULE",
    "TARGET_KEY",
    "migrate_tools_module_config",
]
