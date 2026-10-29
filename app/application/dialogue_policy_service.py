"""对话策略服务：把「默认 → 账号 → 群」三层覆盖读出来、写回去。

- **读**：``resolve()`` 调纯函数 `domain/dialogue_policy.resolve_policy`，
  默认值来自能力自身配置（agent: runtime.config；模块: 模块配置/权限）；
- **写**：只接受白名单键（``SCOPE_OVERRIDABLE`` + ``PERMISSION_KEYS``），
  ``None`` 表示"删掉这层覆盖、恢复继承"；
- **临时静音**：``muted_until`` 到点自动恢复（读时比较时间，无后台任务）。

不在这里做的事：
- 权限身份本身（owner/manager 名册）→ 身份层（docs/identity-and-capability-console.md）；
- 跨账号"谁响应"（channel_policies）→ responder，正交。
"""

from __future__ import annotations

import time
from typing import Any

from app.core.logger import logger
from app.domain.dialogue_policy import (
    PERMISSION_KEYS,
    SCENE_GROUP,
    SCENE_PRIVATE,
    SCOPE_OVERRIDABLE,
    ResolvedPolicy,
    is_overridable,
    legacy_trigger_keys,
    resolve_policy,
    trigger_mode_from,
    validate_override_values,
)

DEFAULT_CAPABILITY = "agent"
SOURCE_MUTED = "muted"

#: 场景层（群聊/私聊）的「账号默认」开关：群行选「继承」时用的就是它。
#: 对应 ``channel_overrides.channel_id = '__group__' / '__private__'``。
SCENE_NAMES: dict[str, str] = {"group": SCENE_GROUP, "private": SCENE_PRIVATE}


class PolicyError(ValueError):
    """策略写入被拒绝（带稳定错误码，供 API 直接回给前端）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DialoguePolicyService:
    def __init__(self, config_service: Any, agent_manager: Any = None, runtime: Any = None) -> None:
        self.config_service = config_service
        self.agent_manager = agent_manager
        #: 直接持有运行时（流水线路径用）：避免为拿一份配置再去 agent_manager 反查
        self.runtime = runtime

    # ==================== 读 ====================

    def _defaults(
        self, account_id: str, capability: str, channel_id: str | None, scene: str | None = None,
    ) -> tuple[dict, bool]:
        """(能力自身配置, 默认开关)。读不到就退回安全默认，绝不抛错。"""
        if capability == DEFAULT_CAPABILITY:
            runtime = self.runtime
            if runtime is None and self.agent_manager is not None:
                try:
                    runtime = self.agent_manager.get_runtime(account_id)
                except Exception as e:  # noqa: BLE001 —— 运行时未装配按"没有配置"处理
                    logger.add_info("Policy").debug(f"[Policy] 取运行时失败 {account_id}: {e}")
                    runtime = None
            config_getter = getattr(getattr(runtime, "config", None), "raw_config", None)
            config = dict(config_getter) if isinstance(config_getter, dict) else {}
            is_group = channel_id is not None or scene in ("group", SCENE_GROUP)
            if is_group:
                return config, bool(config.get("group_enable", False))
            return config, bool(config.get("private_enable", True))

        config = self.config_service.get_module_config(capability, account_id) or {}
        authority = self.config_service.get_module_authority(capability, account_id) or {}
        return dict(config), bool(authority.get("enabled", True))

    def resolve(
        self,
        account_id: object,
        capability: str = DEFAULT_CAPABILITY,
        channel_id: object | None = None,
        *,
        scene: str | None = None,
    ) -> ResolvedPolicy:
        """解析生效策略。

        ``scene`` 缺省按 channel 推断（有群号 = 群聊，否则私聊）；显式传入时用于
        "算群聊默认值"（``scene="group"`` + 无 channel），而不误用私聊默认。
        """
        aid = str(account_id)
        capability = str(capability or DEFAULT_CAPABILITY)
        channel = None if channel_id in (None, "") else str(channel_id)
        scene_key = scene or (SCENE_GROUP if channel else SCENE_PRIVATE)
        scene_norm = SCENE_GROUP if scene_key in ("group", SCENE_GROUP) else scene_key
        defaults, default_enabled = self._defaults(aid, capability, channel, scene_norm)
        account_row = self.config_service.get_channel_override(aid, capability, "")
        scene_row = self.config_service.get_channel_override(aid, capability, scene_norm)
        channel_row = (
            self.config_service.get_channel_override(aid, capability, channel)
            if channel and channel not in (SCENE_GROUP, SCENE_PRIVATE)
            else None
        )

        resolved = resolve_policy(
            capability,
            account_id=aid,
            channel_id=channel,
            defaults=defaults,
            default_enabled=default_enabled,
            account_row=account_row,
            scene_row=scene_row,
            channel_row=channel_row,
        )

        # 临时静音：在最终值上叠加（来源标记为 muted，UI 才能显示"静音中"）
        muted_until = int((channel_row or {}).get("muted_until") or 0)
        if muted_until and time.time() < muted_until:
            return ResolvedPolicy(
                capability=resolved.capability,
                account_id=resolved.account_id,
                channel_id=resolved.channel_id,
                enabled=False,
                enabled_source=SOURCE_MUTED,
                config=resolved.config,
                permission=resolved.permission,
                sources={**resolved.sources, "enabled": SOURCE_MUTED},
            )
        return resolved

    def overrides(
        self,
        account_id: object,
        capability: str = DEFAULT_CAPABILITY,
        channel_id: object | None = None,
    ) -> dict:
        aid = str(account_id)
        channel = "" if channel_id in (None, "") else str(channel_id)
        return {
            "account": self.config_service.get_channel_override(aid, capability, ""),
            "channel": self.config_service.get_channel_override(aid, capability, channel) if channel else None,
        }

    # ==================== 写 ====================

    @staticmethod
    def _validate_keys(capability: str, config: dict | None, permission: dict | None) -> None:
        for key in (config or {}):
            if not is_overridable(capability, key):
                allowed = ", ".join(SCOPE_OVERRIDABLE.get(capability, ())) or "（无）"
                raise PolicyError(
                    "POLICY_KEY_NOT_OVERRIDABLE",
                    f"「{key}」不支持按群覆盖，只能在账号级配置。可覆盖项：{allowed}",
                )
        for key in (permission or {}):
            if key not in PERMISSION_KEYS:
                raise PolicyError(
                    "POLICY_KEY_NOT_OVERRIDABLE",
                    f"「{key}」不是可覆盖的权限项。可覆盖项：{', '.join(PERMISSION_KEYS)}",
                )
        # 白名单只管"哪些键"，值合不合法要单独校验（否则会写进库、运行时静默失效）
        value_errors = validate_override_values(config)
        if value_errors:
            raise PolicyError("POLICY_BAD_VALUE", "；".join(value_errors))

    async def update(
        self,
        account_id: object,
        capability: str = DEFAULT_CAPABILITY,
        channel_id: object | None = None,
        *,
        enabled: Any = "__unset__",
        config: dict | None = None,
        permission: dict | None = None,
        clear: list[str] | None = None,
    ) -> dict:
        """写一层覆盖。

        - ``enabled``：``True/False`` 覆盖；``None`` = 恢复继承；
        - ``config``/``permission``：**逐键合并**（只改给出的键）；
        - ``clear``：要删掉的键（恢复继承）。
        """
        aid = str(account_id)
        capability = str(capability or DEFAULT_CAPABILITY)
        channel = "" if channel_id in (None, "") else str(channel_id)
        self._validate_keys(capability, config, permission)

        row = self.config_service.get_channel_override(aid, capability, channel) or {
            "enabled": None, "config": {}, "permission": {}, "muted_until": 0,
        }
        merged_config = dict(row.get("config") or {})
        merged_permission = dict(row.get("permission") or {})
        merged_config.update({k: v for k, v in (config or {}).items() if v is not None})
        merged_permission.update({k: v for k, v in (permission or {}).items() if v is not None})
        clear_keys = list(clear or [])
        if "trigger_mode" in clear_keys:
            # 清触发方式 = 这一层不再管触发：连双写出去的旧键一起清，
            # 否则旧键会把"恢复继承"又折算回一个显式 mode（用户看起来像没生效）。
            clear_keys.extend(["trigger_at", "trigger_keyword", "trigger_probability"])
        for key in clear_keys:
            merged_config.pop(key, None)
            merged_permission.pop(key, None)

        # 触发方式双写：新前端只写 trigger_mode，旧前端只写 trigger_at/keyword。
        # - 写了 trigger_mode → 同步旧键，旧 UI/脚本读到的仍是同一套语义；
        # - 只写了旧键 → 丢掉显式 mode，让解析时按旧键重新折算。
        incoming = {k: v for k, v in (config or {}).items() if v is not None}
        if "trigger_mode" in incoming:
            mode = str(incoming["trigger_mode"]).strip().lower()
            merged_config.update(legacy_trigger_keys(mode))
            merged_config["trigger_mode"] = trigger_mode_from(merged_config)
        elif "trigger_at" in incoming or "trigger_keyword" in incoming:
            merged_config.pop("trigger_mode", None)

        # enabled 哨兵：区分"没传"与"显式 null（恢复继承）"
        enabled_value: Any
        if enabled == "__unset__":
            enabled_value = row.get("enabled")
        else:
            enabled_value = None if enabled is None else bool(enabled)

        return await self.config_service.save_channel_override(
            aid, capability, channel,
            enabled=enabled_value,
            config=merged_config,
            permission=merged_permission,
        )

    async def bulk_set_enabled(
        self,
        account_id: object,
        capability: str,
        channel_ids: list[str],
        enabled: bool | None,
    ) -> int:
        """批量设置一批群的开关（``enabled=None`` 时清掉覆盖，恢复继承）。"""
        changed = 0
        for channel_id in channel_ids or []:
            channel = str(channel_id or "").strip()
            if not channel:
                continue
            if enabled is None:
                if await self.config_service.delete_channel_override(account_id, capability, channel):
                    changed += 1
                continue
            await self.config_service.save_channel_override(
                account_id, capability, channel, enabled=bool(enabled)
            )
            changed += 1
        return changed

    async def set_mute(
        self,
        account_id: object,
        capability: str,
        channel_id: object,
        minutes: int,
    ) -> dict:
        """临时静音：``minutes<=0`` 取消静音。"""
        channel = str(channel_id or "").strip()
        if not channel:
            raise PolicyError("POLICY_CHANNEL_REQUIRED", "静音需要指定群号")
        until = 0 if minutes <= 0 else int(time.time()) + int(minutes) * 60
        return await self.config_service.save_channel_override(
            account_id, capability, channel, muted_until=until
        )

    async def set_scene_enabled(
        self,
        account_id: object,
        scene: str,
        enabled: bool | None,
        capability: str = DEFAULT_CAPABILITY,
    ) -> dict:
        """写「账号默认」（场景层开关）：群聊/私聊在账号上的默认值。

        这是继承链里最靠近用户的一层：群行选「继承」时用它，群行选「允许/禁止」
        时被群级覆盖压过。``enabled=None`` = 删掉本层覆盖，回到 Agent 配置的
        ``group_enable`` / ``private_enable``（"跟随 Agent 配置"）。
        """
        name = str(scene or "").strip().lower()
        if name not in SCENE_NAMES:
            raise PolicyError("POLICY_BAD_SCENE", "scene 只能是 group 或 private")
        if enabled is not None and not isinstance(enabled, bool):
            raise PolicyError("POLICY_BAD_BODY", "enabled 只能是 true / false / null")
        channel = SCENE_NAMES[name]
        if enabled is None:
            await self.config_service.delete_channel_override(account_id, capability, channel)
            row = None
        else:
            row = await self.config_service.save_channel_override(
                account_id, capability, channel, enabled=enabled
            )
        return {"scene": name, "override": row}

    # ==================== 矩阵（列表页） ====================

    def matrix(self, account_id: object, capability: str = DEFAULT_CAPABILITY) -> dict:
        """群列表 + 每群生效状态（供「群聊开关」矩阵渲染）。

        返回 ``{"default": {...}, "rows": [...]}``：``default`` 是没有群级覆盖时的
        账号级结果，前端据此显示"默认：允许/不允许"。
        """
        aid = str(account_id)
        rows = self.config_service.list_known_groups(aid)
        existing = {
            row["channel_id"]: row
            for row in self.config_service.list_channel_overrides(aid, capability)
            if row["channel_id"]
        }
        # 默认层取"群聊场景"的账号级结果（private_enable 与群聊无关，不能用它当默认）
        default_policy = self.resolve(aid, capability, None, scene="group")
        # 场景层是否显式设过：null = 跟随 Agent 配置（前端据此渲染三态）
        scene_row = self.config_service.get_channel_override(aid, capability, SCENE_GROUP) or {}
        scene_enabled = scene_row.get("enabled")
        out: list[dict] = []
        for group in rows:
            channel = group["group_id"]
            resolved = self.resolve(aid, capability, channel)
            override = existing.get(channel) or {}
            out.append({
                "group_id": channel,
                "name": group["name"],
                "member_count": group["member_count"],
                "in_group": group["in_group"],
                "enabled": resolved.enabled,
                "enabled_source": resolved.enabled_source,
                "muted_until": int(override.get("muted_until") or 0),
                # 生效触发方式：列表页直接展示，不必再为每个群发一次 resolve
                "trigger_mode": resolved.config.get("trigger_mode", "none"),
                "trigger_source": resolved.sources.get("trigger_mode", "default"),
                "trigger_probability": resolved.config.get("trigger_probability", 100),
                "quiet_hours": str(resolved.config.get("quiet_hours") or ""),
                "overridden_keys": sorted(set(override.get("config") or {}) | set(override.get("permission") or {})),
            })
        return {
            "default": {
                "enabled": default_policy.enabled,
                "enabled_source": default_policy.enabled_source,
                # 账号默认（群聊场景层）的显式值：null = 跟随 Agent 配置
                "scene_enabled": None if scene_enabled is None else bool(scene_enabled),
                # 账号默认（群聊场景）的触发方式：群行「继承」时看到的就是它
                "trigger_mode": default_policy.config.get("trigger_mode", "none"),
                "trigger_source": default_policy.sources.get("trigger_mode", "default"),
                "trigger_probability": default_policy.config.get("trigger_probability", 100),
            },
            "rows": out,
        }
