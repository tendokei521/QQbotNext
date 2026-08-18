"""Provider 运行时管理器（对齐 AstrBot ProviderManager 的精简版）。

- 维护 model_id -> provider instance 映射
- 负责把连接预设（source） + 模型实例（provider model）合并成完整 provider 配置
- 提供 get_models / test / reload 能力
"""

from __future__ import annotations

import os
from typing import Any

from app.core.logger import logger
from app.infrastructure.config.config_service import ConfigService
from . import get_provider, get_provider_class


def _resolve_env_keys(config: dict) -> dict:
    """把 key 列表中的 $ENV 占位符替换为环境变量值。"""
    result = dict(config)
    keys = result.get("key") or result.get("api_key", "")
    if isinstance(keys, list):
        resolved = []
        for item in keys:
            if isinstance(item, str) and item.startswith("$"):
                env_val = os.getenv(item[1:], "")
                resolved.append(env_val)
            else:
                resolved.append(item)
        result["key"] = resolved
    elif isinstance(keys, str) and keys.startswith("$"):
        result["api_key"] = os.getenv(keys[1:], "")
    return result


class ProviderRuntimeManager:
    """按 Provider 模型实例管理运行时的轻量 Provider 池。"""

    def __init__(self, config_service: ConfigService) -> None:
        self.config_service = config_service
        self._instances: dict[str, Any] = {}

    # ── 配置解析 ───────────────────────────────────────
    def resolve_provider_config(self, model_id: str) -> dict | None:
        """合并连接预设 + 模型实例，返回可直接传给 Provider 的完整配置。"""
        model = self.config_service.get_provider_model(model_id)
        if model is None:
            return None
        preset = self.config_service.get_provider_preset(model.get("preset_id", ""))
        if preset is None:
            return None

        merged = {
            **(preset.get("config", {}) or {}),
            **(model.get("config", {}) or {}),
        }
        merged["provider"] = preset.get("provider", "openai")
        merged["model"] = model.get("model", "")
        merged["provider_preset_id"] = preset.get("id", "")
        merged["provider_model_id"] = model.get("id", "")
        return _resolve_env_keys(merged)

    def resolve_preset_config(self, preset_id: str) -> dict | None:
        """只解析连接预设（不包含具体模型），用于拉取模型列表等。"""
        preset = self.config_service.get_provider_preset(preset_id)
        if preset is None:
            return None
        config = {
            **(preset.get("config", {}) or {}),
            "provider": preset.get("provider", "openai"),
        }
        return _resolve_env_keys(config)

    # ── 实例管理 ───────────────────────────────────────
    def get_provider(self, model_id: str):
        """获取（或创建）模型对应的 Provider 实例。"""
        if model_id in self._instances:
            return self._instances[model_id]
        config = self.resolve_provider_config(model_id)
        if config is None:
            return None
        instance = get_provider(config)
        self._instances[model_id] = instance
        return instance

    def invalidate(self, model_id: str | None = None) -> None:
        """使缓存实例失效；下次调用会按最新配置重建。"""
        if model_id:
            self._instances.pop(model_id, None)
        else:
            self._instances.clear()

    def shutdown(self) -> None:
        for instance in self._instances.values():
            terminate = getattr(instance, "terminate", None)
            if callable(terminate):
                try:
                    terminate()
                except Exception:
                    pass
        self._instances.clear()

    # ── 模型能力 ───────────────────────────────────────
    async def fetch_models(self, preset_id: str) -> list[str]:
        """拉取连接预设下可用的模型列表。"""
        config = self.resolve_preset_config(preset_id)
        if config is None:
            raise ValueError(f"Provider 预设不存在: {preset_id}")
        cls = get_provider_class(config.get("provider", "openai"))
        instance = cls(config)
        try:
            models = await instance.get_models()
            return models or []
        finally:
            terminate = getattr(instance, "terminate", None)
            if callable(terminate):
                await terminate() if hasattr(terminate, "__await__") else terminate()

    async def test_model(self, model_id: str) -> dict:
        """测试模型实例连接。"""
        config = self.resolve_provider_config(model_id)
        if config is None:
            raise ValueError(f"Provider 模型不存在: {model_id}")
        instance = get_provider(config)
        # 优先用 /models 做连接测试：不消耗对话 token，也不会触发 reasoning 长输出
        try:
            remote_models = await instance.get_models()
            if remote_models:
                return {"ok": True, "message": "连接正常", "reply": "", "models": remote_models[:5]}
        except Exception as e:
            logger.debug(f"[ProviderModel] /models 测试不可用，回退 chat: {e}")

        # 回退：最小 chat 请求；只关心是否成功返回，不关心是否真的生成了文本
        try:
            response = await instance.chat(
                [{"role": "user", "content": "PONG"}],
                model=config.get("model", "deepseek-chat"),
                temperature=0,
                max_tokens=1,
                timeout=int(config.get("timeout", 20) or 20),
            )
        except Exception as e:
            return {"ok": False, "message": str(e)}
        if response.ok or response.raw is not None:
            return {"ok": True, "message": "连接正常", "reply": response.text}
        return {"ok": False, "message": "连接失败，请检查 Key / Base URL / 模型名"}