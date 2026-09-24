"""Provider 注册表与统一入口。

对齐 AstrBot 的 adapter 注册思路：连接预设里的 provider 类型先归一化，
再通过 PROVIDERS 映射到具体适配器类；未知类型回退 OpenAI 兼容。
"""

from __future__ import annotations

import asyncio
import inspect

from .base import BaseProvider, LLMResponse, StreamEvent, format_usage, has_token_usage, merge_usage
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


def _accepts_kwarg(fn, name: str) -> bool:
    """Provider 的 chat 是否接受某关键字参数。

    第三方通过 ``register_provider`` 注册的适配器可能没有 ``max_tool_rounds`` 形参；
    直接传会抛 TypeError，而调用方（chat_with_fallback）的异常兜底会把它吞成
    “换下一个 Provider”，表现为静默降级。这里先内省一次，不支持就不传。
    """
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        # 无法内省（C 扩展/装饰器遮蔽）时按“接受”处理，由调用方异常兜底
        return True
    if name in params:
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def _model_label(chain: list[dict], fallback: str | None = None) -> str:
    """请求日志里用的模型名（优先 provider 预设的 provider_model_id）。"""
    for cfg in chain or []:
        name = str((cfg or {}).get("provider_model_id") or (cfg or {}).get("model") or "").strip()
        if name:
            return name
    return str(fallback or "").strip()


def log_token_usage(
    usage: dict | None,
    *,
    model: str = "",
    chars: int | None = None,
    stream: bool = False,
) -> None:
    """一次请求**完全结束后**（含工具轮/重试）info 一次本次 token 消耗。

    - 上游给什么就记什么（输入/输出/命中/未命中/合计，见 ``base.format_usage``）；
    - 上游一个字都没报就**不打这一行**——宁缺毋编，避免日志里出现编造的 token 数；
    - ``chars`` / ``model`` 只作上下文，便于和"API 请求 -> session"那行对上。
    """
    if not has_token_usage(usage):
        return
    from app.llm import logger

    text = f"本次请求消耗: {format_usage(usage)}"
    if chars is not None:
        text += f" | 回复 {chars} 字符"
    if model:
        text += f" | model={model}"
    text += " | 流式" if stream else " | 非流式"
    logger.add_info("Api").info(text)


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
    max_tool_rounds: int = 5,
    max_empty_retries: int = 1,
    empty_retry_backoff: float = 0.4,
) -> LLMResponse:
    """按顺序尝试 config_chain，直到某个 provider 成功返回。

    “成功但空内容”不算成功：``max_empty_retries`` 控制同 provider 的空回复重试次数
    （默认 1 次，退避 ``empty_retry_backoff`` 秒），仍空则换下一个 provider。
    全部软失败时返回最后一个响应（保留 raw），让调用方的兜底分支继续生效。
    """
    chain = list(config_chain or [])
    if not chain:
        return LLMResponse(text="", raw=None)
    last: LLMResponse | None = None
    last_empty: LLMResponse | None = None
    retries = max(0, int(max_empty_retries or 0))
    delay = max(0.0, float(empty_retry_backoff or 0.0))
    # 本次请求（含空回复重试与回退下一个 provider）的 token 消耗：结束后 info 一次
    total_usage: dict = {}
    returned: LLMResponse | None = None

    try:
        for cfg in chain:
            provider = get_provider(cfg)
            for attempt in range(retries + 1):
                try:
                    kwargs: dict = {}
                    if _accepts_kwarg(provider.chat, "max_tool_rounds"):
                        kwargs["max_tool_rounds"] = max_tool_rounds
                    resp = await provider.chat(
                        messages,
                        model=cfg.get("model") or model,
                        temperature=cfg.get("temperature", temperature),
                        max_tokens=cfg.get("max_tokens", max_tokens),
                        timeout=int(cfg.get("timeout", timeout) or timeout),
                        tools=tools,
                        tool_executor=tool_executor,
                        **kwargs,
                    )
                except Exception as e:
                    from app.llm import logger
                    from .base import format_llm_error

                    logger.add_info("LLM").warning(
                        f"回退：模型 {cfg.get('provider_model_id', cfg.get('model'))} 请求异常: {format_llm_error(e)}"
                    )
                    last = LLMResponse(text="", raw=None)
                    break
                last = resp
                merge_usage(total_usage, resp.usage)
                if resp.ok or resp.raw is not None:
                    # 空内容但仍算请求成功：重试同 provider，避免偶现的空回复直接兜底
                    if _response_is_empty(resp) and attempt < retries:
                        from app.llm import logger

                        logger.add_info("LLM").warning(
                            f"空回复重试 [{attempt + 1}/{retries}]：模型 "
                            f"{cfg.get('provider_model_id', cfg.get('model'))}"
                        )
                        if delay:
                            await asyncio.sleep(delay)
                        continue
                    if _response_is_empty(resp):
                        last_empty = resp
                        break
                    returned = resp
                    return resp
                break
        returned = last_empty or last or LLMResponse(text="", raw=None)
        return returned
    finally:
        log_token_usage(
            total_usage,
            model=_model_label(chain, model),
            chars=len(returned.text) if returned is not None else None,
        )


def _response_is_empty(resp: LLMResponse | None) -> bool:
    """响应是否“成功但没有任何可展示内容”。

    只有工具结果、没有文本的轮次不算空回复（那是正常的工具循环中间态）。
    """
    if resp is None:
        return True
    if (resp.text or "").strip():
        return False
    if (getattr(resp, "reasoning", "") or "").strip():
        return False
    return not resp.tool_results


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
    max_empty_retries: int = 1,
    empty_retry_backoff: float = 0.4,
    usage_sink: dict | None = None,
):
    """按顺序尝试 config_chain 的流式 provider；仅在首个事件前出错才切换到下一个。

    额外处理「连上了但什么都没产出」：流式请求成功结束却既无文本也无工具调用时，
    同 provider 重试 ``max_empty_retries`` 次（退避 ``empty_retry_backoff`` 秒），
    再换下一个 provider。只重试“没产出任何事件”的情况——一旦有 tool_call 或文本产出，
    重试就可能重复执行工具/重复发送，绝不能做。

    token 消耗：上游在流末尾回报的 usage 会累加，**本次流式请求结束时 info 一次**。
    带 ``usage_sink`` 时改为写进调用方的 dict（不在这里打日志）——流式的工具循环在
    调用方，由它把多轮汇总后只打一行。
    """
    chain = list(config_chain or [])
    retries = max(0, int(max_empty_retries or 0))
    delay = max(0.0, float(empty_retry_backoff or 0.0))
    total_usage: dict = {}
    text_chars = 0

    try:
        for cfg in chain:
            provider = get_provider(cfg)
            for attempt in range(retries + 1):
                started = False
                got_content = False
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
                        if ev.type in ("text", "tool_call"):
                            got_content = True
                        # 第三方适配器可能自造事件对象：usage 用 getattr 兜底
                        event_usage = getattr(ev, "usage", None)
                        if event_usage:
                            merge_usage(total_usage, event_usage)
                            # 带 sink 时**立刻**回写（不等 finally）：客户端提前 break
                            # 也能拿到已产出的 usage，调用方随后可以正常打一行汇总
                            if usage_sink is not None:
                                merge_usage(usage_sink, event_usage)
                        if ev.type == "text" and getattr(ev, "text", ""):
                            text_chars += len(ev.text)
                        started = True
                        yield ev
                except Exception as e:
                    from app.llm import logger
                    from .base import format_llm_error

                    logger.add_info("LLM").warning(
                        f"流式回退：模型 {cfg.get('provider_model_id', cfg.get('model'))} 请求异常: "
                        f"{format_llm_error(e)}"
                    )
                    if started:
                        raise
                    break  # 换下一个 provider
                if got_content:
                    return
                if attempt < retries:
                    from app.llm import logger

                    logger.add_info("LLM").warning(
                        f"空回复重试 [{attempt + 1}/{retries}]：模型 "
                        f"{cfg.get('provider_model_id', cfg.get('model'))} 流式无产出"
                    )
                    if delay:
                        await asyncio.sleep(delay)
                    continue
                # 该 provider 用尽重试：留给调用方的兜底文本，继续尝试下一个 provider
                break
    finally:
        # 带 sink 时调用方自己打一行（流式的工具循环在它那边）；这里只负责"没有 sink"的直调
        if usage_sink is None:
            log_token_usage(
                total_usage,
                model=_model_label(chain, model),
                chars=text_chars or None,
                stream=True,
            )


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
    "log_token_usage",
    "PROVIDERS",
    "PROVIDER_ALIASES",
]