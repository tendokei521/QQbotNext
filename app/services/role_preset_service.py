"""角色预设服务：独立管理可复用的角色系统提示词。

每个角色预设只保存名称、描述与 system_prompt；Agent 配置通过
「应用到当前 Agent」把预设提示词写入当前 bot 的 system_prompt。
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from app.core.logger import logger
from app.infrastructure.config.config_service import ConfigService


class RolePresetService:
    """角色预设 CRUD。"""

    def __init__(self, config_service: ConfigService) -> None:
        self.config_service = config_service

    def list_presets(self) -> list[dict]:
        return self.config_service.list_role_presets()

    def get_preset(self, role_id: str) -> dict | None:
        return self.config_service.get_role_preset(role_id)

    async def create_preset(self, name: str, description: str = "", system_prompt: str = "") -> dict:
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
            "updated_at": int(time.time()),
        }
        await self.config_service.save_role_preset(role_id, updated)
        logger.info(f"[RolePreset] 更新角色 {role_id}")
        return dict(updated)

    async def delete_preset(self, role_id: str) -> None:
        deleted = await self.config_service.delete_role_preset(role_id)
        if not deleted:
            raise ValueError(f"角色预设不存在: {role_id}")
        logger.info(f"[RolePreset] 删除角色 {role_id}")