"""从 runtime 读「这一轮用哪个 provider / 能用哪些模态」。

## 为什么单独成文件

三个纯读函数，被 `prompt_prep`（取值）与 `chat`（请求）两边用。留在 `chat` 里会让
`prompt_prep` 反向依赖 `chat` 形成环——拆包时「谁依赖谁」必须单向。
"""

from __future__ import annotations

from app.domain.modalities import normalize_modalities


def _provider_config_for(module) -> dict:
    """取传给 Provider 的配置：优先走运行时合并（Provider 预设），兼容旧对象。"""
    if hasattr(module, "provider_config"):
        return module.provider_config()
    config = getattr(module, "config", None)
    if hasattr(config, "raw_config"):
        return dict(config.raw_config)
    return dict(config or {})


def _provider_chain_for(module) -> list[dict]:
    """取按顺序尝试的 provider 配置链（主模型 + 回退模型）；旧对象退化为单条配置。"""
    if hasattr(module, "provider_chain"):
        return module.provider_chain()
    return [_provider_config_for(module)]


def _modalities_for_chain(provider_chain: list[dict]) -> list[str] | None:
    """取主模型声明的模态能力；未配置时返回 None（兼容旧行为=全部放行）。"""
    if not provider_chain:
        return None
    return normalize_modalities((provider_chain[0] or {}).get("modalities"))
