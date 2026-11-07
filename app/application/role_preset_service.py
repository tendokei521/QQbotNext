"""角色预设服务：独立管理可复用的角色系统提示词。

每个角色预设保存名称、描述、system_prompt，以及可选的
``begin_dialogs``（预设对话，对齐 AstrBot persona）/ ``tools`` / ``skills``；
Agent 配置通过「应用到当前 Agent」把预设提示词写入当前 bot 的 system_prompt。
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from app.core.logger import logger
from app.infrastructure.config.config_service import ConfigService


def normalize_begin_dialogs(value: Any) -> list[dict]:
    """归一化预设对话：只保留 ``{role: user|assistant, content: 非空字符串}``。

    对齐 AstrBot persona 的 ``begin_dialogs``（组装时前插到历史最前、不落库）；
    这里只负责存储与校验，如何进 prompt 由装配器/插件决定。
    """
    result: list[dict] = []
    for item in value or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "") or "").strip().lower()
        content = str(item.get("content", "") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        result.append({"role": role, "content": content})
    return result


def normalize_name_list(value: Any) -> list[str] | None:
    """归一化工具/技能名列表：``None`` = 不限制；``[]`` = 显式禁用；字符串按单元素处理。"""
    if value is None:
        return None
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return None
    names = [str(v).strip() for v in value if str(v or "").strip()]
    return list(dict.fromkeys(names))  # 去重保序


def with_extended_defaults(preset: dict | None) -> dict | None:
    """补齐扩展字段：旧预设（库里没有这些键）读出来也有一致的形状。"""
    if preset is None:
        return None
    return {
        **preset,
        "begin_dialogs": normalize_begin_dialogs(preset.get("begin_dialogs")),
        "tools": normalize_name_list(preset.get("tools")),
        "skills": normalize_name_list(preset.get("skills")),
    }


class RolePresetService:
    """角色预设 CRUD。"""

    #: 旧人设自动迁移的一次性标记前缀（kv，按账号）。迁移完成/判定不需要迁移后写一次，
    #: 用户把迁移出来的预设删掉后，下次读配置不会又重建一份。
    LEGACY_MIGRATED_KV_PREFIX = "legacy_role_preset_migrated:"

    #: 自动迁移创建的角色预设使用的内部描述（同名预设据此区分"是我们建的"还是用户建的）
    LEGACY_PRESET_DESCRIPTION = "自动兼容保存的历史角色设定"

    def __init__(self, config_service: ConfigService) -> None:
        self.config_service = config_service

    def list_presets(self) -> list[dict]:
        return [with_extended_defaults(p) or {} for p in self.config_service.list_role_presets()]

    def get_preset(self, role_id: str) -> dict | None:
        return with_extended_defaults(self.config_service.get_role_preset(role_id))

    async def create_preset(
        self,
        name: str,
        description: str = "",
        system_prompt: str = "",
        *,
        begin_dialogs: Any = None,
        tools: Any = None,
        skills: Any = None,
    ) -> dict:
        name = str(name or "").strip()
        system_prompt = str(system_prompt or "").strip()
        if not name:
            raise ValueError("角色名称不能为空")
        if not system_prompt:
            raise ValueError("系统提示词不能为空")
        role_id = str(uuid.uuid4().hex[:12])
        now = int(time.time())
        preset = {
            "id": role_id,
            "name": name,
            "description": str(description or "").strip(),
            "system_prompt": system_prompt,
            "begin_dialogs": normalize_begin_dialogs(begin_dialogs),
            "tools": normalize_name_list(tools),
            "skills": normalize_name_list(skills),
            "created_at": now,
            "updated_at": now,
        }
        await self.config_service.save_role_preset(role_id, preset)
        logger.info(f"[RolePreset] 创建角色 {name} ({role_id})")
        return dict(preset)

    async def update_preset(self, role_id: str, data: dict[str, Any]) -> dict:
        old = self.config_service.get_role_preset(role_id)
        if old is None:
            raise ValueError(f"角色预设不存在: {role_id}")
        name = str(data.get("name", old.get("name", ""))).strip()
        system_prompt = str(data.get("system_prompt", old.get("system_prompt", ""))).strip()
        if not name:
            raise ValueError("角色名称不能为空")
        if not system_prompt:
            raise ValueError("系统提示词不能为空")
        updated = {
            **old,
            "name": name,
            "description": str(data.get("description", old.get("description", "")) or "").strip(),
            "system_prompt": system_prompt,
            "begin_dialogs": normalize_begin_dialogs(data.get("begin_dialogs", old.get("begin_dialogs"))),
            "tools": normalize_name_list(data.get("tools", old.get("tools"))),
            "skills": normalize_name_list(data.get("skills", old.get("skills"))),
            "updated_at": int(time.time()),
        }
        await self.config_service.save_role_preset(role_id, updated)
        logger.info(f"[RolePreset] 更新角色 {role_id}")
        return dict(updated)

    async def ensure_legacy_prompt(self, bot_id: Any) -> dict | None:
        """兼容旧格式：把「用户自己写过的旧 system_prompt」一次性保存成角色预设。

        ## 迁移条件（2026-11-07 收窄）

        此前这里只看"账号里有没有 system_prompt"，而旧的保存路径会把内置默认值落成
        显式键、人格副本也会被同步写入，于是**每次打开 AI 设置都会凭空多出一份**
        ``{bot_id}角色设定``；用户删掉后下次读配置又重建。现在只在确实存在旧式人设时迁移：

        1. 账号**没有**绑定人格预设（``persona_preset_id`` 为空）——绑定了预设时人设来自
           预设，账号里的 ``system_prompt`` 只是代码维护的兜底副本，迁移它只会多一份重复；
        2. 内容**不是**内置默认值——默认值是旧保存路径顺手写进去的，不是用户写的人设；
        3. 该账号还没迁移过（kv 标记）——迁移完写标记，用户删掉预设后不会再出现。

        同名预设若已存在且不是本迁移创建的（描述不是内部标记），**不动它**，避免覆盖用户数据。

        Returns:
            迁移/找到的角色预设；不需要迁移或已迁移过时返回 ``None``。
        """
        stored = self.config_service.get_module_config("agent", bot_id) or {}
        if str(stored.get("persona_preset_id") or "").strip():
            return None
        prompt = str(stored.get("system_prompt") or "").strip()
        if not prompt or prompt == self._default_system_prompt():
            return None

        marker = f"{self.LEGACY_MIGRATED_KV_PREFIX}{bot_id}"
        if await self._kv_flag(marker):
            return None

        target_name = f"{bot_id}角色设定"
        existing = next((p for p in self.list_presets() if p.get("name") == target_name), None)
        if existing is not None:
            if existing.get("description") != self.LEGACY_PRESET_DESCRIPTION:
                # 用户自己建的同名预设：不覆盖、也不重复创建，标记为已处理即可。
                await self._set_kv_flag(marker)
                return None
            if existing.get("system_prompt") != prompt:
                result = await self.update_preset(existing["id"], {
                    "name": target_name,
                    "system_prompt": prompt,
                })
            else:
                result = existing
        else:
            result = await self.create_preset(
                target_name, self.LEGACY_PRESET_DESCRIPTION, prompt
            )
        await self._set_kv_flag(marker)
        return result

    @staticmethod
    def _default_system_prompt() -> str:
        """内置默认人设（旧保存路径可能把它落成显式键，不算用户写的人设）。"""
        from app.agent.config import DEFAULT_LLM_CONFIG

        return str(DEFAULT_LLM_CONFIG.get("system_prompt") or "").strip()

    async def _kv_flag(self, key: str) -> bool:
        """读迁移标记（kv 读失败按"没迁移过"处理，不阻断页面加载）。"""
        try:
            return bool(await self.config_service.get_kv(key, ""))
        except Exception as e:  # noqa: BLE001 —— 迁移标记是旁路，读不到只影响去重
            logger.debug(f"[RolePreset] 读取迁移标记失败（按未迁移处理）: {e}")
            return False

    async def _set_kv_flag(self, key: str) -> None:
        """写迁移标记（失败不影响已经完成的迁移，最多下次重复一次同样的判定）。"""
        try:
            await self.config_service.set_kv(key, "1")
        except Exception as e:  # noqa: BLE001 —— 标记写失败只影响"删了不再长"的去重
            logger.debug(f"[RolePreset] 写入迁移标记失败（已忽略）: {e}")

    async def delete_preset(self, role_id: str) -> None:
        deleted = await self.config_service.delete_role_preset(role_id)
        if not deleted:
            raise ValueError(f"角色预设不存在: {role_id}")
        # 清理 Agent 配置里的悬挂引用：否则组装时静默回退 system_prompt，
        # 用户看到的是"人设没变"，却查不到原因（P4-5）。
        for bot_id, config in list(self.config_service.get_all_module_configs("agent").items()):
            if str(config.get("persona_preset_id", "")) == str(role_id):
                new_config = dict(config)
                new_config.pop("persona_preset_id", None)
                await self.config_service.save_module_config("agent", bot_id, new_config)
        logger.info(f"[RolePreset] 删除角色 {role_id}")