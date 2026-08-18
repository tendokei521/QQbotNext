"""Provider 模型实例 / 全局设置服务。

对齐 AstrBot 的 provider + provider_settings 两层：
- provider_presets = source 连接配置
- provider_models = 挂在该连接下的具体模型/Provider 实例
- provider_settings = 默认模型 / fallback / pool
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from app.core.logger import logger
from app.infrastructure.config.config_service import ConfigService
from app.llm.providers.runtime_manager import ProviderRuntimeManager

PASSWORD_MASK = "••••••••"


def _mask_model_config(config: dict) -> dict:
    result = dict(config or {})
    for key in ("api_key", "key", "token", "secret"):
        if result.get(key):
            result[key] = PASSWORD_MASK
    return result


def _public_model(model: dict) -> dict:
    return {
        **dict(model),
        "config": _mask_model_config(model.get("config", {}) or {}),
    }


class ProviderModelService:
    """Provider 模型实例与全局设置。"""

    def __init__(self, config_service: ConfigService, runtime_manager: ProviderRuntimeManager) -> None:
        self.config_service = config_service
        self.runtime_manager = runtime_manager

    # ── 模型实例 ───────────────────────────────────────
    def list_models(self, preset_id: str | None = None) -> list[dict]:
        return [_public_model(m) for m in self.config_service.list_provider_models(preset_id)]

    def get_model(self, model_id: str, masked: bool = True) -> dict | None:
        model = self.config_service.get_provider_model(model_id)
        if model is None:
            return None
        return _public_model(model) if masked else model

    def list_references(self, model_id: str) -> list[tuple[str, str]]:
        refs = []
        for bot_id, config in self.config_service.get_all_module_configs("agent").items():
            if str(config.get("provider_model_id", "")) == str(model_id):
                refs.append((str(bot_id), str(model_id)))
        return refs

    async def create_model(self, preset_id: str, data: dict) -> dict:
        if not self.config_service.get_provider_preset(preset_id):
            raise ValueError(f"Provider 预设不存在: {preset_id}")
        model_name = str(data.get("model", "")).strip()
        if not model_name:
            raise ValueError("模型名称不能为空")
        model_id = str(data.get("id") or uuid.uuid4().hex[:12])
        now = int(time.time())
        model = {
            "id": model_id,
            "preset_id": preset_id,
            "model": model_name,
            "provider_type": str(data.get("provider_type", "chat")).strip() or "chat",
            "enabled": bool(data.get("enabled", True)),
            "config": {
                "temperature": float(data.get("temperature", 0.7) or 0.7),
                "max_tokens": int(data.get("max_tokens", 1024) or 1024),
            },
            "created_at": now,
            "updated_at": now,
        }
        await self.config_service.save_provider_model(model_id, model)
        logger.info(f"[ProviderModel] 创建模型 {model_name} ({model_id}) @ {preset_id}")
        self.runtime_manager.invalidate(model_id)
        return _public_model(model)

    async def update_model(self, model_id: str, data: dict) -> dict:
        old = self.config_service.get_provider_model(model_id)
        if old is None:
            raise ValueError(f"Provider 模型不存在: {model_id}")
        config = dict(old.get("config", {}) or {})
        if "temperature" in data:
            config["temperature"] = float(data.get("temperature", 0.7) or 0.7)
        if "max_tokens" in data:
            config["max_tokens"] = int(data.get("max_tokens", 1024) or 1024)
        updated = {
            **old,
            "model": str(data.get("model", old.get("model", ""))).strip() or old.get("model", ""),
            "provider_type": str(data.get("provider_type", old.get("provider_type", "chat"))).strip() or "chat",
            "enabled": bool(data.get("enabled", old.get("enabled", True))),
            "config": config,
            "updated_at": int(time.time()),
        }
        if not updated["model"]:
            raise ValueError("模型名称不能为空")
        await self.config_service.save_provider_model(model_id, updated)
        logger.info(f"[ProviderModel] 更新模型 {model_id}")
        self.runtime_manager.invalidate(model_id)
        return _public_model(updated)

    async def delete_model(self, model_id: str) -> None:
        refs = self.list_references(model_id)
        if refs:
            bot_labels = ", ".join(f"Bot {bot_id}" for bot_id, _ in refs[:5])
            more = f" 等 {len(refs)} 个" if len(refs) > 5 else ""
            raise ValueError(f"模型正被 {bot_labels}{more} Agent 使用，请先解除引用")
        deleted = await self.config_service.delete_provider_model(model_id)
        if not deleted:
            raise ValueError(f"Provider 模型不存在: {model_id}")
        self.runtime_manager.invalidate(model_id)

    async def fetch_models(self, preset_id: str) -> list[str]:
        return await self.runtime_manager.fetch_models(preset_id)

    async def test_model(self, model_id: str) -> dict:
        return await self.runtime_manager.test_model(model_id)

    # ── 全局设置 ───────────────────────────────────────
    def get_settings(self) -> dict:
        return self.config_service.get_provider_settings()

    async def save_settings(self, settings: dict) -> dict:
        await self.config_service.save_provider_settings(settings or {})
        return self.config_service.get_provider_settings()

    # ── 迁移 ───────────────────────────────────────────
    async def migrate_legacy_models(self) -> int:
        """把 Agent 配置中的 provider_preset_id + model 转成 provider_models 记录。"""
        migrated = 0
        presets = self.config_service.list_provider_presets()
        preset_ids = {p["id"] for p in presets}
        for bot_id, config in list(self.config_service.get_all_module_configs("agent").items()):
            if config.get("provider_model_id"):
                continue
            preset_id = str(config.get("provider_preset_id", "") or "")
            model_name = str(config.get("model", "") or "").strip()
            if preset_id not in preset_ids or not model_name:
                continue
            existing = next(
                (m for m in self.config_service.list_provider_models(preset_id)
                 if m.get("model") == model_name),
                None,
            )
            if existing is None:
                model_id = f"auto_{uuid.uuid4().hex[:8]}"
                now = int(time.time())
                model = {
                    "id": model_id,
                    "preset_id": preset_id,
                    "model": model_name,
                    "provider_type": "chat",
                    "enabled": True,
                    "config": {
                        "temperature": float(config.get("temperature", 0.7) or 0.7),
                        "max_tokens": int(config.get("max_tokens", 1024) or 1024),
                    },
                    "created_at": now,
                    "updated_at": now,
                }
                await self.config_service.save_provider_model(model_id, model)
                existing = model
            new_config = dict(config)
            new_config["provider_model_id"] = existing["id"]
            # 保留旧字段也无害，但后续优先使用 provider_model_id
            await self.config_service.save_module_config("agent", bot_id, new_config)
            migrated += 1
            logger.info(f"[ProviderModel] Agent Bot {bot_id} 已迁移到模型 {existing['id']}")
        return migrated