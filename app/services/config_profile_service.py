"""配置档案与路由服务（对齐 AstrBot abconf + routing 的精简版）。

每个配置档案是一组可复用的 Agent/Provider 配置快照；
路由把 UMO（group_xxx / private_xxx）绑定到某个档案。
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from app.core.logger import logger
from app.infrastructure.config.config_service import ConfigService


def _public_profile(profile: dict) -> dict:
    return dict(profile)


class ConfigProfileService:
    """配置档案 CRUD 与路由管理。"""

    def __init__(self, config_service: ConfigService) -> None:
        self.config_service = config_service

    # ── 档案 ───────────────────────────────────────────
    def list_profiles(self) -> list[dict]:
        return [_public_profile(p) for p in self.config_service.list_config_profiles()]

    def get_profile(self, profile_id: str) -> dict | None:
        profile = self.config_service.get_config_profile(profile_id)
        return _public_profile(profile) if profile else None

    async def create_profile(self, name: str, config: dict | None = None) -> dict:
        name = str(name or "").strip()
        if not name:
            raise ValueError("档案名称不能为空")
        profile_id = str(uuid.uuid4().hex[:12])
        profile = {
            "id": profile_id,
            "name": name,
            "config": config or {},
            "updated_at": int(time.time()),
        }
        await self.config_service.save_config_profile(profile_id, profile)
        logger.info(f"[ConfigProfile] 创建档案 {name} ({profile_id})")
        return _public_profile(profile)

    async def update_profile(self, profile_id: str, data: dict) -> dict:
        old = self.config_service.get_config_profile(profile_id)
        if old is None:
            raise ValueError(f"配置档案不存在: {profile_id}")
        updated = {
            **old,
            "name": str(data.get("name", old.get("name", ""))).strip() or old.get("name", ""),
            "config": data.get("config", old.get("config", {}) or {}),
            "updated_at": int(time.time()),
        }
        if not updated["name"]:
            raise ValueError("档案名称不能为空")
        await self.config_service.save_config_profile(profile_id, updated)
        logger.info(f"[ConfigProfile] 更新档案 {profile_id}")
        return _public_profile(updated)

    async def delete_profile(self, profile_id: str) -> None:
        deleted = await self.config_service.delete_config_profile(profile_id)
        if not deleted:
            raise ValueError(f"配置档案不存在: {profile_id}")
        logger.info(f"[ConfigProfile] 删除档案 {profile_id}")

    # ── 路由 ───────────────────────────────────────────
    def list_routes(self) -> dict[str, str]:
        return self.config_service.get_config_routes()

    def get_route(self, umo: str) -> str | None:
        return self.config_service.get_config_routes().get(umo)

    async def set_route(self, umo: str, profile_id: str) -> None:
        if self.config_service.get_config_profile(profile_id) is None:
            raise ValueError(f"配置档案不存在: {profile_id}")
        await self.config_service.set_config_route(str(umo).strip(), profile_id)
        logger.info(f"[ConfigProfile] 路由 {umo} -> {profile_id}")

    async def delete_route(self, umo: str) -> None:
        await self.config_service.delete_config_route(umo)
        logger.info(f"[ConfigProfile] 删除路由 {umo}")