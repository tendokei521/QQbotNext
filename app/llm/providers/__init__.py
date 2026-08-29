"""Provider 注册表与统一入口。

对齐 AstrBot 的 adapter 注册思路：连接预设里的 provider 类型先归一化，
再通过 PROVIDERS 映射到具体适配器类；未知类型回退 OpenAI 兼容。
"""

from __future__ import annotations

from .base import BaseProvider, LLMResponse, StreamEvent
from .embedding import OpenAIEmbeddingProvider
from .openai_compat import OpenAICompatProvider
from .anthropic import AnthropicProvider
from .gemini import GeminiProvider
from .rerank import get_rerank_provider
from .stt import OpenAIWhisperSTTProvider
from .tts import OpenAITTSProvider

PROVIDERS: dict[str, type[BaseProvider]] = {
    "openai": OpenAICompatProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
}

# 常见 OpenAI 兼容别名：在适配器真正拆分前，统一映射到 openai 兼容实现
PROVIDER_ALIASES: dict[str, str] = {
    "deepseek": "openai",
    "openrouter": "openai",
    "moonshot": "openai",
    "zhipu": "openai",
    "ollama": "openai",
    "lm_studio": "openai",
    "siliconflow": "openai",
    "dashscope": "openai",
    "nvidia": "openai",
    "groq": "openai",
    "xai": "openai",
    "aihubmix": "openai",
    "modelscope": "openai",
    "302ai": "openai",
    "ppio": "openai",
    "tokenpony": "openai",
    "compshare": "openai",
    "claude": "anthropic",
    "google": "gemini",
}


def normalize_provider_type(provider: str) -> str:
    """把别名归一化为已注册的适配器类型。"""
    provider = (provider or "openai").strip().lower()
    return PROVIDER_ALIASES.get(provider, provider)


def get_provider_class(provider: str) -> type[BaseProvider]:
    """按 provider 类型返回适配器类；未知类型回退 openai。"""
    return PROVIDERS.get(normalize_provider_type(provider), OpenAICompatProvider)


def register_provider(
    name: str,
    cls: type[BaseProvider],
    aliases: tuple[str, ...] = (),
) -> None:
    """运行期注册一个新的 Provider 适配器，供第三方模块/插件调用。"""
    name = (name or "").strip().lower()
    if not name or not cls:
        raise ValueError("register_provider 需要 name 和 provider class")
    PROVIDERS[name] = cls
    for alias in aliases:
        alias = (alias or "").strip().lower()
        if alias:
            PROVIDER_ALIASES[alias] = name


def provider_supports(config: dict, capability: str) -> bool:
    """按配置判断 provider 是否支持某项能力（chat / stream / embedding 等）。"""
    try:
        provider = get_provider(config)
        return bool(getattr(provider, "supports", lambda _c: False)(capability))
    except Exception:
        return False


def get_provider(config: dict) -> BaseProvider:
    """按 config.provider_type + config.provider 返回对应能力 Provider。"""
    provider_type = str((config or {}).get("provider_type", "chat")).lower()
    if provider_type == "embedding":
        return OpenAIEmbeddingProvider(config)
    if provider_type == "rerank":
        return get_rerank_provider(config)
    if provider_type == "tts":
        return OpenAITTSProvider(config)
    if provider_type == "stt":
        return OpenAIWhisperSTTProvider(config)
    provider_name = (config or {}).get("provider", "openai")
    cls = get_provider_class(provider_name)
    return cls(config)


async def chat_with_fallback(
    config_chain: list[dict],
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 1024,
    timeout: int = 30,
    tools: list[dict] | None = None,
    tool_executor=None,
) -> LLMResponse:
    """按顺序尝试 config_chain，直到某个 provider 成功返回（包括空文本但请求成功）。"""
    last: LLMResponse | None = None
    for cfg in config_chain:
        provider = get_provider(cfg)
        try:
            resp = await provider.chat(
                messages,
                model=cfg.get("model") or model,
                temperature=cfg.get("temperature", temperature),
                max_tokens=cfg.get("max_tokens", max_tokens),
                timeout=int(cfg.get("timeout", timeout) or timeout),
                tools=tools,
                tool_executor=tool_executor,
            )
        except Exception as e:
            from app.llm import logger
            from .base import format_llm_error

            logger.add_info("LLM").warning(
                f"回退：模型 {cfg.get('provider_model_id', cfg.get('model'))} 请求异常: {format_llm_error(e)}"
            )
            last = LLMResponse(text="", raw=None)
            continue
        if resp.ok or resp.raw is not None:
            return resp
        last = resp
    return last or LLMResponse(text="", raw=None)


async def iter_stream_with_fallback(
    config_chain: list[dict],
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 1024,
    timeout: int = 30,
    tools: list[dict] | None = None,
    tool_executor=None,
):
    """按顺序尝试 config_chain 的流式 provider；仅在首个事件前出错才切换到下一个。"""
    for cfg in config_chain:
        provider = get_provider(cfg)
        started = False
        try:
            async for ev in provider.chat_stream(
                messages,
                model=cfg.get("model") or model,
                temperature=cfg.get("temperature", temperature),
                max_tokens=cfg.get("max_tokens", max_tokens),
                timeout=int(cfg.get("timeout", timeout) or timeout),
                tools=tools,
                tool_executor=tool_executor,
            ):
                started = True
                yield ev
        except Exception as e:
            from app.llm import logger
            from .base import format_llm_error

            logger.add_info("LLM").warning(
                f"流式回退：模型 {cfg.get('provider_model_id', cfg.get('model'))} 请求异常: {format_llm_error(e)}"
            )
            if started:
                raise
            continue
        return


__all__ = [
    "BaseProvider",
    "LLMResponse",
    "StreamEvent",
    "OpenAICompatProvider",
    "get_provider",
    "get_provider_class",
    "normalize_provider_type",
    "register_provider",
    "provider_supports",
    "chat_with_fallback",
    "iter_stream_with_fallback",
    "PROVIDERS",
    "PROVIDER_ALIASES",
]