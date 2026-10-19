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


#: ``usage_sink`` 里的保留键：本次请求**实际服务**的模型名（不是回退链里第一个）。
#: 它是字符串、不是 token 数，所以 merge_usage / format_usage 都当没看见；
#: 只给调用方（流式工具循环在调用方）拼日志/遥测用。
SERVED_MODEL_KEY = "_served_model"


def _cfg_model_label(cfg: dict | None, fallback: str | None = None) -> str:
    """单个 provider 配置对应的模型名（优先 provider 预设的 provider_model_id）。

    注意取的是**具体配置**而非 ``chain[0]``：回退链上真正服务请求的可能是后面的
    backup 配置，日志/遥测若固定写第一个就会把 backup 的消耗记在主模型头上。
    """
    name = str((cfg or {}).get("provider_model_id") or (cfg or {}).get("model") or "").strip()
    return name or str(fallback or "").strip()


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
    # 真正服务本次请求的配置（空回复/异常回退后**不是** chain[0]）：日志必须写它，
    # 否则会把回退模型（如 backup）的消耗记在主模型（primary）头上。
    served_cfg: dict | None = None

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
                # 回了响应（哪怕空/无 choices）= 该 provider 真的被请求过：token 消耗
                # 里可能就有它一份，日志名字要记它而不是 chain[0]
                served_cfg = cfg
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
                # resp.ok 与 raw 都为假（上游没返回 choices）：换下一个 provider
                break
        returned = last_empty or last or LLMResponse(text="", raw=None)
        return returned
    finally:
        log_token_usage(
            total_usage,
            model=_cfg_model_label(served_cfg, model),
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
    调用方，由它把多轮汇总后只打一行。此时 ``usage_sink[SERVED_MODEL_KEY]`` 会被写成
    实际服务本轮的模型名，调用方据此拼日志/遥测，避免回退后仍记主模型名。
    """
    chain = list(config_chain or [])
    retries = max(0, int(max_empty_retries or 0))
    delay = max(0.0, float(empty_retry_backoff or 0.0))
    total_usage: dict = {}
    text_chars = 0
    # 实际服务本轮的配置（空回复/异常回退后**不是** chain[0]）
    served_cfg: dict | None = None

    #: 整条链最后一个真实错误：全都起不来时交出去，别让真因随"空回复"一起消失
    last_error = ""
    try:
        for cfg in chain:
            provider = get_provider(cfg)
            provider_error = ""
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
                        if ev.type == "error":
                            if got_content:
                                # 已产出过正文后中途断流：如实上报，由调用方收尾
                                yield ev
                                return
                            # 一个正文字都没有就报错 = 这个 provider 起不来。
                            # **不能**把 error 事件透出去：消费端（chat.stream_response /
                            # initiative_stream）一收到 error 就 break，生成器就此挂起，
                            # 下面"换下一个 provider"的逻辑再也走不到 —— 多 provider
                            # 回退链于是形同虚设，备用模型一次都不会被请求。
                            provider_error = ev.text or "provider error"
                            break
                        if ev.type in ("text", "tool_call", "reasoning"):
                            got_content = True
                            # 思考也算产出：上游确实在跑，只是把预算花在 reasoning 里。
                            # 同一条 prompt 原样重试必然再打满一次（线上为此白烧了 17.5 万
                            # token 仍拿不到正文），所以不在这里重试；正文为空的收敛重试
                            # 交给调用方（它能改 prompt：追加「直接作答」提醒）。
                            # 有产出 = 这个 provider 真在服务本轮：立刻定名，客户端提前
                            # break 也能拿到（调用方随后打的那一行要写对模型）
                            served_cfg = cfg
                            if usage_sink is not None:
                                usage_sink[SERVED_MODEL_KEY] = _cfg_model_label(cfg, model)
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
                        # 已经产出过事件：本轮由它服务，异常上抛但名字要留住
                        served_cfg = cfg
                        if usage_sink is not None:
                            usage_sink[SERVED_MODEL_KEY] = _cfg_model_label(cfg, model)
                        raise
                    provider_error = provider_error or format_llm_error(e)
                    break  # 换下一个 provider
                if got_content:
                    return
                if provider_error:
                    # 确定性失败：同 provider 原样重试没有意义（它内部已按 retry_attempts
                    # 试过），直接换下一个
                    break
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
            if provider_error:
                from app.llm import logger

                last_error = provider_error
                logger.add_info("LLM").warning(
                    f"流式回退：模型 {cfg.get('provider_model_id', cfg.get('model'))} 失败，"
                    f"换下一个: {provider_error}"
                )
        # 整条链都没产出：把最后一个真实错误交出去。否则调用方只看到"空回复"，
        # 上游到底报了什么（429 / 余额不足 / 模型不存在）彻底丢失。
        if last_error:
            yield StreamEvent(type="error", text=last_error)
    finally:
        # 带 sink 时调用方自己打一行（流式的工具循环在它那边）；这里只负责"没有 sink"的直调
        if usage_sink is None:
            log_token_usage(
                total_usage,
                model=_cfg_model_label(served_cfg, model),
                chars=text_chars or None,
                stream=True,
            )
        elif SERVED_MODEL_KEY not in usage_sink:
            # 全部 provider 零产出：没有「实际服务者」，退化为最后一个尝试过的
            last_cfg = chain[-1] if chain else None
            usage_sink[SERVED_MODEL_KEY] = _cfg_model_label(served_cfg or last_cfg, model)


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
    "SERVED_MODEL_KEY",
    "PROVIDERS",
    "PROVIDER_ALIASES",
]