"""Provider 运行时管理器（对齐 AstrBot ProviderManager 的精简版）。

- 维护 model_id -> provider instance 映射
- 负责把连接预设（source） + 模型实例（provider model）合并成完整 provider 配置
- 提供 get_models / test / reload 能力
"""

from __future__ import annotations

import os
from typing import Any

from app.agent.providers import get_provider, get_provider_class
from app.agent.providers.policy import merge_config_layers
from app.core.logger import logger
from app.infrastructure.config.config_service import ConfigService


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
        """合并连接预设 + 模型实例，返回可直接传给 Provider 的完整配置。

        合并逻辑统一走 ``request_policy.merge_config_layers``：这里此前有一份**手写**的
        ``{**preset, **model}``，与 ``AgentRuntime._config_for_model_id`` 的版本等价但不
        同源——两份会漂移，而且这一份没有逐键溯源（``_max_tokens_origin``）。
        """
        model = self.config_service.get_provider_model(model_id)
        if model is None:
            return None
        preset = self.config_service.get_provider_preset(model.get("preset_id", ""))
        if preset is None:
            return None

        merged = merge_config_layers(
            preset_layer=preset.get("config", {}) or {},
            model_layer=model.get("config", {}) or {},
            meta={
                "provider": preset.get("provider", "openai"),
                "provider_type": model.get("provider_type", "chat"),
                "model": model.get("model", ""),
                "provider_preset_id": preset.get("id", ""),
                "provider_model_id": model.get("id", ""),
            },
        ).config
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
                except Exception as e:
                    logger.debug(f"[ProviderRuntime] 终止实例失败（已忽略）: {e}")
        self._instances.clear()

    # ── 模型能力 ───────────────────────────────────────
    async def fetch_models(self, preset_id: str) -> list[str]:
        """拉取连接预设下可用的模型列表。"""
        return [entry["id"] for entry in await self.fetch_model_catalog(preset_id)]

    async def fetch_model_catalog(self, preset_id: str, provider_type: str = "chat") -> list[dict]:
        """拉取模型目录：``[{"id", "modalities", "source"}]``。

        ``modalities`` 是**推断出来**的建议（见 ``model_capabilities``），只在"添加模型"
        时用于预勾选；``source`` 说明结论来自厂商元数据、模型名还是默认值。
        """
        from app.agent.providers.model_capabilities import infer_modalities

        config = self.resolve_preset_config(preset_id)
        if config is None:
            raise ValueError(f"Provider 预设不存在: {preset_id}")
        cls = get_provider_class(config.get("provider", "openai"))
        instance = cls(config)
        try:
            getter = getattr(instance, "get_model_catalog", None)
            entries = await getter() if callable(getter) else []
            if not entries:
                entries = [{"id": model} for model in (await instance.get_models() or [])]
        finally:
            terminate = getattr(instance, "terminate", None)
            if callable(terminate):
                await terminate() if hasattr(terminate, "__await__") else terminate()

        catalog: list[dict] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            raw = entry.get("raw") if isinstance(entry.get("raw"), dict) else {}
            model_id = str(entry.get("id") or raw.get("id") or "").strip()
            if not model_id:
                continue
            payload = raw if raw.get("id") else {**raw, "id": model_id}
            modalities, source = infer_modalities(payload, provider_type)
            catalog.append({"id": model_id, "modalities": modalities, "source": source})
        return catalog

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

        # 非 chat 能力（embedding/rerank/tts/stt）没有 /models 可测时，仅校验配置已加载
        if not hasattr(instance, "chat"):
            return {"ok": True, "message": "能力配置已加载", "reply": ""}

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