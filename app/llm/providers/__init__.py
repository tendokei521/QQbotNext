"""Provider 注册表。"""

from .base import BaseProvider, LLMResponse, StreamEvent
from .openai_compat import OpenAICompatProvider

PROVIDERS = {"openai": OpenAICompatProvider}


def get_provider(config: dict) -> BaseProvider:
    """按 config.provider 返回 Provider 实例；未知类型回退 openai。"""
    provider_name = (config or {}).get("provider", "openai")
    cls = PROVIDERS.get(provider_name, OpenAICompatProvider)
    return cls(config)


__all__ = ["BaseProvider", "LLMResponse", "StreamEvent", "OpenAICompatProvider", "get_provider", "PROVIDERS"]
