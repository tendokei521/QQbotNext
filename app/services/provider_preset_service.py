"""Provider 预设服务：把 LLM 连接配置从 Agent 配置中独立出来。

每个预设表示一组可复用的“连哪里”配置（api_base / api_key / 重试次数等），
Agent 只需要通过 provider_preset_id 引用它，不再重复填写连接信息。
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from app.infrastructure.config.config_service import ConfigService
from app.llm.providers import get_provider
from app.llm.providers.base import format_llm_error
from app.core.logger import logger

PASSWORD_MASK = "••••••••"

# 预设配置里对外必须脱敏的字段
_SECRET_FIELDS = {"api_key", "key", "api_token", "token", "secret"}


def _masked_config(config: dict) -> dict:
    """复制配置并把敏感字段打码。"""
    result = dict(config or {})
    for key in _SECRET_FIELDS:
        if result.get(key):
            result[key] = PASSWORD_MASK
    return result


def _public_preset(preset: dict) -> dict:
    """返回预设给前端（深拷贝）。

    按用户要求：预设管理页需要直接编辑 API Key，因此不再打码。
    """
    return {
        **dict(preset),
        "config": dict(preset.get("config", {}) or {}),
    }


class ProviderPresetService:
    """Provider 预设的 CRUD、测试与旧配置迁移。"""

    def __init__(self, config_service: ConfigService) -> None:
        self.config_service = config_service

    # ── 查询 ───────────────────────────────────────────
    def list_presets(self) -> list[dict]:
        """列出全部预设（对外脱敏）。"""
        return [_public_preset(p) for p in self.config_service.list_provider_presets()]

    def get_preset(self, preset_id: str, masked: bool = True) -> dict | None:
        """按 ID 获取预设；masked=True 时返回脱敏副本，否则返回含密钥的内部副本。"""
        preset = self.config_service.get_provider_preset(preset_id)
        if preset is None:
            return None
        return _public_preset(preset) if masked else preset

    def list_references(self, preset_id: str) -> list[tuple[str, str]]:
        """返回引用该预设的 Agent 配置位置列表 [(bot_id, preset_id)]。"""
        refs = []
        for bot_id, config in self.config_service.get_all_module_configs("agent").items():
            if str(config.get("provider_preset_id", "")) == str(preset_id):
                refs.append((str(bot_id), str(preset_id)))
        return refs

    # ── 写入 ───────────────────────────────────────────
    async def create_preset(self, data: dict) -> dict:
        """创建预设，返回新预设（对外脱敏）。"""
        name = str(data.get("name", "")).strip()
        provider = str(data.get("provider", "openai")).strip() or "openai"
        config = data.get("config") or {}
        if not isinstance(config, dict):
            raise ValueError("config 必须是对象")
        if not name:
            raise ValueError("预设名称不能为空")
        if not str(config.get("api_base", "")).strip():
            raise ValueError("API 基础 URL 不能为空")

        now = int(time.time())
        preset_id = str(data.get("id") or uuid.uuid4().hex[:12])
        preset = {
            "id": preset_id,
            "name": name,
            "provider": provider,
            "config": {
                "api_base": str(config.get("api_base", "")).strip().rstrip("/"),
                "api_key": str(config.get("api_key", "")).strip(),
                "retry_attempts": int(config.get("retry_attempts", 3) or 3),
                "timeout": int(config.get("timeout", 30) or 30),
            },
            "enabled": bool(data.get("enabled", True)),
            "created_at": now,
            "updated_at": now,
        }
        await self.config_service.save_provider_preset(preset_id, preset)
        logger.info(f"[ProviderPreset] 创建预设 {name} ({preset_id})")
        return _public_preset(preset)

    async def update_preset(self, preset_id: str, data: dict) -> dict:
        """更新预设；api_key 为脱敏哨兵时保留旧值。"""
        old = self.config_service.get_provider_preset(preset_id)
        if old is None:
            raise ValueError(f"Provider 预设不存在: {preset_id}")

        config = data.get("config") or {}
        if not isinstance(config, dict):
            raise ValueError("config 必须是对象")

        old_config = old.get("config", {}) or {}
        merged_config = dict(old_config)
        if "api_base" in config:
            merged_config["api_base"] = str(config.get("api_base", "")).strip().rstrip("/")
        if "api_key" in config:
            if config.get("api_key") == PASSWORD_MASK:
                merged_config["api_key"] = old_config.get("api_key", "")
            else:
                merged_config["api_key"] = str(config.get("api_key", "")).strip()
        if "retry_attempts" in config:
            merged_config["retry_attempts"] = int(config.get("retry_attempts", 3) or 3)
        if "timeout" in config:
            merged_config["timeout"] = int(config.get("timeout", 30) or 30)

        updated = {
            **old,
            "name": str(data.get("name", old.get("name", ""))).strip() or old.get("name", ""),
            "provider": str(data.get("provider", old.get("provider", "openai"))).strip() or old.get("provider", "openai"),
            "config": merged_config,
            "enabled": bool(data.get("enabled", old.get("enabled", True))),
            "updated_at": int(time.time()),
        }
        if not updated["name"]:
            raise ValueError("预设名称不能为空")
        if not str(merged_config.get("api_base", "")).strip():
            raise ValueError("API 基础 URL 不能为空")

        await self.config_service.save_provider_preset(preset_id, updated)
        logger.info(f"[ProviderPreset] 更新预设 {updated['name']} ({preset_id})")
        return _public_preset(updated)

    async def delete_preset(self, preset_id: str) -> None:
        """删除预设；相关模型一并删除，Agent/档案/全局设置中的引用恢复为空。"""
        models = self.config_service.list_provider_models(preset_id)
        model_ids = {m["id"] for m in models}

        # 1. 清理 Agent 配置引用
        for bot_id, config in list(self.config_service.get_all_module_configs("agent").items()):
            new_config = dict(config)
            if str(new_config.get("provider_preset_id", "")) == preset_id:
                new_config.pop("provider_preset_id", None)
            if str(new_config.get("provider_model_id", "")) in model_ids:
                new_config.pop("provider_model_id", None)
            if new_config != config:
                await self.config_service.save_module_config("agent", bot_id, new_config)

        # 2. 清理配置档案引用
        for profile in self.config_service.list_config_profiles():
            profile_config = profile.get("config", {}) or {}
            if not isinstance(profile_config, dict):
                continue
            new_profile_config = dict(profile_config)
            if str(new_profile_config.get("provider_preset_id", "")) == preset_id:
                new_profile_config.pop("provider_preset_id", None)
            if str(new_profile_config.get("provider_model_id", "")) in model_ids:
                new_profile_config.pop("provider_model_id", None)
            if new_profile_config != profile_config:
                profile["config"] = new_profile_config
                profile["updated_at"] = int(time.time())
                await self.config_service.save_config_profile(profile["id"], profile)

        # 3. 清理全局默认设置引用
        settings = self.config_service.get_provider_settings()
        new_settings = dict(settings)
        if str(new_settings.get("default_preset_id", "")) == preset_id:
            new_settings["default_preset_id"] = ""
        if str(new_settings.get("default_model_id", "")) in model_ids:
            new_settings["default_model_id"] = ""
        if new_settings != settings:
            await self.config_service.save_provider_settings(new_settings)

        # 4. 删除该预设下的模型实例
        for model in models:
            await self.config_service.delete_provider_model(model["id"])

        # 5. 删除预设本身
        deleted = await self.config_service.delete_provider_preset(preset_id)
        if not deleted:
            raise ValueError(f"Provider 预设不存在: {preset_id}")
        logger.info(f"[ProviderPreset] 删除预设 {preset_id}（级联清理 {len(models)} 个模型）")

    # ── 测试 ───────────────────────────────────────────
    async def test_preset(self, preset_id: str, model: str | None = None) -> dict:
        """用预设配置发起一次最小聊天请求，返回测试结果。"""
        preset = self.config_service.get_provider_preset(preset_id)
        if preset is None:
            raise ValueError(f"Provider 预设不存在: {preset_id}")

        config = {
            **(preset.get("config", {}) or {}),
            "provider": preset.get("provider", "openai"),
        }
        # 优先使用该预设下的第一个启用模型，避免测试时模型名不匹配
        models = self.config_service.list_provider_models(preset_id)
        first_model = next((m for m in models if m.get("enabled")), None)
        model_name = model or (first_model.get("model") if first_model else "deepseek-chat")
        if first_model:
            config.update(first_model.get("config", {}) or {})
        provider = get_provider(config)
        # 优先用 /models 做连接测试：不消耗对话 token，也不会触发 reasoning 长输出
        try:
            remote_models = await provider.get_models()
            if remote_models:
                return {"ok": True, "message": "连接正常", "reply": "", "models": remote_models[:5]}
        except Exception as e:
            logger.debug(f"[ProviderPreset] /models 测试不可用，回退 chat: {format_llm_error(e)}")

        # 回退：最小 chat 请求；只关心是否成功返回，不关心是否真的生成了文本
        try:
            response = await provider.chat(
                [{"role": "user", "content": "PONG"}],
                model=model_name,
                temperature=0,
                max_tokens=1,
                timeout=int(config.get("timeout", 20) or 20),
            )
        except Exception as e:
            logger.error(f"[ProviderPreset] 测试失败 {preset_id}: {format_llm_error(e)}")
            return {"ok": False, "message": format_llm_error(e)}

        # 请求已成功返回（raw 非空）即视为连接正常；
        # 某些模型 max_tokens 被 reasoning 占用时 text 可能为空，不应误报失败。
        if response.ok or response.raw is not None:
            return {"ok": True, "message": "连接正常", "reply": response.text}
        return {"ok": False, "message": "连接失败：没有收到有效回复，请检查 API Key / Base URL / 模型名"}

    # ── 旧配置迁移 ─────────────────────────────────────
    async def migrate_legacy_agent_configs(self) -> int:
        """把 Agent 配置里旧的内联 api_key/api_base 自动迁移为 Provider 预设。

        只在 Agent 尚未选择 provider_preset_id 且存在旧 api_key 时执行。
        返回迁移的 Agent 数量。
        """
        migrated = 0
        presets = self.config_service.list_provider_presets()
        for bot_id, config in list(self.config_service.get_all_module_configs("agent").items()):
            if config.get("provider_preset_id"):
                continue
            api_key = str(config.get("api_key", "") or "").strip()
            if not api_key:
                continue
            api_base = str(config.get("api_base", "") or "https://api.deepseek.com").strip().rstrip("/")

            match = None
            for preset in presets:
                if (
                    preset.get("provider") == config.get("provider", "openai")
                    and str(((preset.get("config") or {}).get("api_base", "") or "")).rstrip("/") == api_base
                    and str(((preset.get("config") or {}).get("api_key", "") or "")) == api_key
                ):
                    match = preset
                    break

            now = int(time.time())
            if match is None:
                preset_id = f"auto_{uuid.uuid4().hex[:8]}"
                preset = {
                    "id": preset_id,
                    "name": f"自动迁移-{config.get('model', 'LLM')}",
                    "provider": config.get("provider", "openai"),
                    "config": {
                        "api_base": api_base,
                        "api_key": api_key,
                        "retry_attempts": int(config.get("retry_attempts", 3) or 3),
                        "timeout": 30,
                    },
                    "enabled": True,
                    "created_at": now,
                    "updated_at": now,
                }
                await self.config_service.save_provider_preset(preset_id, preset)
                presets.append(preset)
                preset_id_used = preset_id
            else:
                preset_id_used = match["id"]

            new_config = dict(config)
            new_config["provider_preset_id"] = preset_id_used
            new_config.pop("api_key", None)
            new_config.pop("api_base", None)
            new_config.pop("provider", None)
            new_config.pop("retry_attempts", None)
            await self.config_service.save_module_config("agent", bot_id, new_config)
            migrated += 1
            logger.info(f"[ProviderPreset] Agent Bot {bot_id} 已迁移到预设 {preset_id_used}")

        if migrated:
            logger.info(f"[ProviderPreset] 旧 Agent 配置迁移完成: {migrated} 个")
        return migrated