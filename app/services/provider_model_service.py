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

from app.domain.modalities import default_modalities_for, normalize_modalities
from app.agent.providers.policy import (
    REASONING_FLOOR,
    is_reasoning_model,
    merge_config_layers,
    plan_max_tokens,
)
from app.agent.providers.runtime_manager import ProviderRuntimeManager
from app.core.logger import logger
from app.infrastructure.config.config_service import ConfigService

PASSWORD_MASK = "••••••••"

#: 模型实例里"三层都能设"的可选覆盖项。全部遵循同一套三态：
#: 缺省 / 空串 = 不覆盖（回落到连接预设 → Agent 页 → 内置默认），数值 = 显式覆盖。
OPTIONAL_OVERRIDE_KEYS = ("max_tokens", "context_window", "temperature", "reasoning")

#: ``max_tokens`` 的硬上限（防止手滑写成一百万）
MAX_TOKENS_CEILING = 1_000_000


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _mask_model_config(config: dict) -> dict:
    result = dict(config or {})
    for key in ("api_key", "key", "token", "secret"):
        if result.get(key):
            result[key] = PASSWORD_MASK
    return result


def _normalize_optional_int(value: Any, field: str = "该项") -> int | None:
    """三态整数：``None``/空串/0 → ``None``（= 不覆盖）；其余取整。

    非法输入（``"abc"`` / 负数）**直接报错**而不是悄悄当成"不覆盖"：静默吞掉用户
    输入正是这套配置一路出问题的原因，手滑写成负数必须当场看得见。
    """
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 需要一个整数，收到 {value!r}") from exc
    return None if number == 0 else number


def _normalize_optional_bool(value: Any) -> bool | None:
    """三态布尔：``None``/空串 = 跟随模型名自动判定。"""
    if value is None or value == "":
        return None
    return bool(value)


def normalize_model_config(data: dict, existing: dict | None = None) -> dict:
    """把前端提交的模型配置归一化成"可落库的 config_json"。

    统一三态语义（此前 create/update 各写一套，且前端无条件送 1024，把 Agent 页的
    「最大输出 Tokens」永久压死）::

        缺省 / "" / 0  →  不写这个键（= 不覆盖，回落到下一层）
        None           →  显式清除已有的覆盖
        数值           →  显式覆盖

    真正的**取值校验**在 ``validate_model_config`` 里，这里只管形状。
    """
    config = dict(existing or {})

    if "temperature" in data:
        raw = data.get("temperature")
        if raw in (None, ""):
            config.pop("temperature", None)
        else:
            config["temperature"] = float(raw or 0.7)

    for key in ("max_tokens", "context_window"):
        if key not in data:
            continue
        label = "最大输出 Token" if key == "max_tokens" else "上下文窗口"
        normalized = _normalize_optional_int(data.get(key), label)
        if normalized is None:
            config.pop(key, None)
        else:
            config[key] = normalized

    if "reasoning" in data:
        flag = _normalize_optional_bool(data.get("reasoning"))
        if flag is None:
            config.pop("reasoning", None)
        else:
            config["reasoning"] = flag

    if "modalities" in data:
        normalized = normalize_modalities(data.get("modalities"))
        if normalized is None:
            config.pop("modalities", None)
        else:
            config["modalities"] = normalized

    return config


def validate_model_config(model_name: str, config: dict) -> None:
    """写入侧校验：把"必然跑不通"的配置挡在落库之前。

    运行时（``request_policy.plan_max_tokens``）也会兜底抬升/封顶，但那只是给存量
    脏数据擦屁股；用户新填的值必须当场说清问题，否则他会以为设了 1024。
    """
    max_tokens = _as_int(config.get("max_tokens"), 0)
    window = _as_int(config.get("context_window"), 0)

    if max_tokens:
        if max_tokens < 1:
            raise ValueError("最大输出 Token 必须大于 0；留空表示跟随下一层设置")
        if max_tokens > MAX_TOKENS_CEILING:
            raise ValueError(f"最大输出 Token 不能超过 {MAX_TOKENS_CEILING}")
        if window and max_tokens >= window:
            raise ValueError(
                f"最大输出 Token（{max_tokens}）不能大于等于上下文窗口（{window}）"
                "——输出也算在窗口里"
            )
    if window and window < 1:
        raise ValueError("上下文窗口必须大于 0；留空表示不限制")

    if is_reasoning_model({"model": model_name, "reasoning": config.get("reasoning")}) and max_tokens:
        if max_tokens < REASONING_FLOOR:
            raise ValueError(
                f"「{model_name}」是思考模型，最大输出 Token 至少要有 {REASONING_FLOOR}"
                f"（你填的 {max_tokens} 会被思考过程吃光，正文必然为空）。"
                f"要么调大到 {REASONING_FLOOR} 以上，要么留空跟随下一层设置。"
            )


def _effective_view(model: dict, preset_config: dict | None) -> dict:
    """给 UI 看的"这个模型当下真正会用什么"。

    只算「连接预设 + 模型实例」这两层（Agent 层属于每个 bot 自己的配置，
    不进模型列表），但足以回答"我填的值有没有被上面一层吃掉"。
    """
    merged = merge_config_layers(
        preset_layer=preset_config or {},
        model_layer=model.get("config", {}) or {},
        meta={"model": model.get("model", ""), "provider_model_id": model.get("id", "")},
    )
    plan = plan_max_tokens(merged.config)
    return {
        "max_tokens": plan.value,
        "max_tokens_origin": plan.origin,
        "max_tokens_notes": list(plan.notes),
        "context_window": merged.context_window,
        "reasoning": is_reasoning_model(merged.config),
        "overrides": sorted(
            key
            for key in OPTIONAL_OVERRIDE_KEYS
            if (model.get("config") or {}).get(key) not in (None, "")
        ),
    }


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

    def _preset_config(self, preset_id: str) -> dict:
        preset = self.config_service.get_provider_preset(preset_id)
        return dict((preset or {}).get("config", {}) or {})

    # ── 模型实例 ───────────────────────────────────────
    def list_models(self, preset_id: str | None = None) -> list[dict]:
        result = []
        for model in self.config_service.list_provider_models(preset_id):
            public = _public_model(model)
            public["effective"] = _effective_view(model, self._preset_config(model.get("preset_id", "")))
            result.append(public)
        return result

    def get_model(self, model_id: str, masked: bool = True) -> dict | None:
        model = self.config_service.get_provider_model(model_id)
        if model is None:
            return None
        if not masked:
            return model
        public = _public_model(model)
        public["effective"] = _effective_view(model, self._preset_config(model.get("preset_id", "")))
        return public

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
        provider_type = str(data.get("provider_type", "chat")).strip() or "chat"
        model_config = normalize_model_config(data)
        model_config.setdefault("temperature", 0.7)
        if not model_config.get("modalities"):
            model_config["modalities"] = default_modalities_for(provider_type)
        # 写入侧校验：把"必然跑不通"的值挡在落库之前。max_tokens **不填就不写**，
        # 交回下一层（连接预设 → Agent 页 → 内置默认）——此前无条件写 1024，
        # 于是每个新建模型都带一个"看起来像用户设置"的 1024，永久压死 Agent 页。
        validate_model_config(model_name, model_config)
        now = int(time.time())
        model = {
            "id": model_id,
            "preset_id": preset_id,
            "model": model_name,
            "provider_type": provider_type,
            "enabled": bool(data.get("enabled", True)),
            "config": model_config,
            "created_at": now,
            "updated_at": now,
        }
        await self.config_service.save_provider_model(model_id, model)
        logger.info(f"[ProviderModel] 创建模型 {model_name} ({model_id}) @ {preset_id}")
        self.runtime_manager.invalidate(model_id)
        return self.get_model(model_id) or _public_model(model)

    async def update_model(self, model_id: str, data: dict) -> dict:
        old = self.config_service.get_provider_model(model_id)
        if old is None:
            raise ValueError(f"Provider 模型不存在: {model_id}")
        config = normalize_model_config(data, old.get("config", {}) or {})
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
        validate_model_config(updated["model"], config)
        await self.config_service.save_provider_model(model_id, updated)
        logger.info(f"[ProviderModel] 更新模型 {model_id}")
        self.runtime_manager.invalidate(model_id)
        return self.get_model(model_id) or _public_model(updated)

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
                    # 不烘 max_tokens：迁移出来的模型不该凭空多一个"用户设置的"预算。
                    # 此前这里写 int(config.get("max_tokens", 1024) or 1024)，
                    # 于是每迁移一个 Bot 就多一行永久压死 Agent 页的 1024。
                    "config": {
                        "temperature": float(config.get("temperature", 0.7) or 0.7),
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
