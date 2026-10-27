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

    async def ensure_legacy_prompt(self, bot_id: Any, system_prompt: str | None) -> dict | None:
        """兼容旧格式：把 Agent 配置里已有的 system_prompt 自动保存为角色预设。

        预设名称固定为 f"{bot_id}角色设定"，幂等：
        - 不存在则创建；
        - 已存在但内容不一致则更新；
        - 内容一致则原样返回。
        """
        prompt = str(system_prompt or "").strip()
        if not prompt:
            return None
        target_name = f"{bot_id}角色设定"
        existing = next((p for p in self.list_presets() if p.get("name") == target_name), None)
        if existing is not None:
            if existing.get("system_prompt") != prompt:
                return await self.update_preset(existing["id"], {
                    "name": target_name,
                    "system_prompt": prompt,
                })
            return existing
        return await self.create_preset(target_name, "自动兼容保存的历史角色设定", prompt)

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