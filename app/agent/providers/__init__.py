"""Provider 注册表与统一入口。

对齐 AstrBot 的 adapter 注册思路：连接预设里的 provider 类型先归一化，
再通过 PROVIDERS 映射到具体适配器类；未知类型回退 OpenAI 兼容。
"""

from __future__ import annotations

import asyncio
import inspect

from app.agent.providers.anthropic import AnthropicProvider
from app.agent.providers.base import BaseProvider, LLMResponse, StreamEvent, format_usage, has_token_usage, merge_usage
from app.agent.providers.embedding import OpenAIEmbeddingProvider
from app.agent.providers.gemini import GeminiProvider
from app.agent.providers.openai_compat import OpenAICompatProvider
from app.agent.providers.policy import (
    Failure,
    FailureKind,
    TurnBudget,
    classify_error_text,
    classify_exception,
    classify_response,
)
from app.agent.providers.rerank import get_rerank_provider
from app.agent.providers.stt import OpenAIWhisperSTTProvider
from app.agent.providers.tts import OpenAITTSProvider

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

#: 值得"同 provider 再试一次"的失败类别（外部条件可能变化的那几类）。
#: ``EMPTY_CUT`` 不在此列：它要的是**更大的预算**，而回退器改不了 max_tokens，
#: 原样重发只会把同样的预算再烧一遍 —— 交给 chat 层（它知道生效预算是多少）。
_RETRY_SAME_KINDS = frozenset({
    FailureKind.TRANSIENT,
    FailureKind.EMPTY_SUSPECT,
})


def _attempt_allowance(max_empty_retries: int, budget) -> int:
    """单 provider 的重试额度：有 TurnBudget 时以档位为准（配置项降为上限）。"""
    configured = max(0, int(max_empty_retries or 0))
    if budget is None:
        return configured
    return min(configured, max(0, int(budget.transient_retries)))


def _should_retry_same(failure, attempt: int, allowance: int, *, provider_failed: bool = False) -> bool:
    """要不要**同 provider**再试一次（``failure`` 为 None 表示有产出）。

    重试必须有且只有一个归属层，否则会相乘烧钱：

    - **HTTP 层瞬态**（429/5xx/超时）由 ``openai_compat`` 内部按 ``retry_attempts``
      带退避重试 —— 它能看到状态码与 ``Retry-After``；
    - **整轮请求次数**由 ``TurnBudget`` 封顶；
    - 所以当错误**已经冒到回退层**（``provider_failed=True``），说明适配器内部的
      瞬态重试已经用尽，这里再试一次只是把同一份上下文重发一遍。此时只做"换下一个
      provider"。

    回退层唯一该重试的是**根本没报错、只是零产出**的情况（网关提前断流／计费了却
    没内容），且只在外部条件可能变化时（瞬态/疑似）——"只有思考没有正文"与"被预算
    截断"都不在此列：前者要换问法、后者要抬预算，都不是原样重发能解决的。
    """
    if failure is None or provider_failed or not failure.retry_same:
        return False
    if failure.kind not in _RETRY_SAME_KINDS:
        return False
    return attempt < allowance


def _retry_delay(failure, default: float) -> float:
    """退避秒数：上游给了 ``Retry-After`` 就听它的。"""
    if failure is not None and failure.retry_after:
        return max(0.0, float(failure.retry_after))
    return max(0.0, float(default or 0.0))


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
    from app.agent import logger

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
    budget: TurnBudget | None = None,
    input_tokens_estimate: int = 0,
) -> LLMResponse:
    """按顺序尝试 config_chain，直到某个 provider 成功返回。

    **重试决策由失败分类决定，不再"空了就原样再来一次"**：

    - 瞬态（429/5xx/超时/网关抖动/计费了却没产出）→ 同 provider 退避重试，听
      ``Retry-After``；用尽后换下一个 provider；
    - 确定性（参数/权限/模型不存在/上下文超长/余额/审核）→ **立刻停整条链**。
      换模型、原样重发都一样失败，只会多烧一遍上下文；
    - 被 max_tokens 截断 → 不在这里重试（回退器改不了预算），记一条可操作日志后
      换下一个 provider，抬预算的事交给 chat 层。

    ``budget`` 给整轮一个总请求次数与累计输入 token 上限：没有这个闸门，
    ``retry_attempts × 空回复重试 × 收敛重试 × provider 数`` 会乘成十几次，
    每次都带着全量上下文（线上为此把 16.7 万 token 发了三遍）。

    全部软失败时返回最后一个响应（保留 raw），让调用方的兜底分支继续生效。
    """
    chain = list(config_chain or [])
    if not chain:
        return LLMResponse(text="", raw=None)
    last: LLMResponse | None = None
    last_empty: LLMResponse | None = None
    retries = max(0, int(max_empty_retries or 0))
    delay = max(0.0, float(empty_retry_backoff or 0.0))
    allowance = _attempt_allowance(retries, budget)
    # 本次请求（含空回复重试与回退下一个 provider）的 token 消耗：结束后 info 一次
    total_usage: dict = {}
    returned: LLMResponse | None = None
    # 真正服务本次请求的配置（空回复/异常回退后**不是** chain[0]）：日志必须写它，
    # 否则会把回退模型（如 backup）的消耗记在主模型（primary）头上。
    served_cfg: dict | None = None
    blocked_reason = ""

    try:
        for cfg in chain:
            provider = get_provider(cfg)
            failure: Failure | None = None
            for attempt in range(allowance + 1):
                if budget is not None:
                    allowed, why = budget.allow(
                        estimated_input=input_tokens_estimate,
                        reason=f"回退链 {_cfg_model_label(cfg, model)} 第 {attempt + 1} 次请求",
                    )
                    if not allowed:
                        from app.agent import logger

                        logger.add_info("LLM").warning(f"请求预算用尽，停止重试：{why}")
                        blocked_reason = why
                        break
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
                    from app.agent import logger
                    from app.agent.providers.base import format_llm_error

                    failure = classify_exception(e)
                    if budget is not None:
                        budget.charge(estimated_input=input_tokens_estimate)
                    logger.add_info("LLM").warning(
                        f"回退：模型 {_cfg_model_label(cfg, model)} 请求异常"
                        f"（{failure.describe()}）: {format_llm_error(e)}"
                    )
                    last = LLMResponse(text="", raw=None)
                    # 适配器内部的瞬态重试已经用尽（见 _should_retry_same 的说明），
                    # 这里只决定"换下一个"还是"整条链停"
                    if failure.deterministic:
                        from app.agent import logger as _lg

                        _lg.add_info("LLM").error(
                            f"确定性失败（{failure.describe()}），停止整条回退链："
                            "换模型或原样重发都不会成功"
                        )
                        returned = last
                        return last
                    break
                last = resp
                merge_usage(total_usage, resp.usage)
                if budget is not None:
                    budget.charge(estimated_input=input_tokens_estimate, usage=resp.usage)
                # 回了响应（哪怕空/无 choices）= 该 provider 真的被请求过：token 消耗
                # 里可能就有它一份，日志名字要记它而不是 chain[0]
                served_cfg = cfg
                failure = classify_response(
                    text=resp.text,
                    reasoning=getattr(resp, "reasoning", "") or "",
                    tool_results=resp.tool_results,
                    finish_reason=str((resp.raw or {}).get("finish_reason", "") or "")
                    if isinstance(resp.raw, dict) else "",
                    usage=resp.usage,
                    max_tokens=int(cfg.get("max_tokens", max_tokens) or 0),
                )
                if failure is None:
                    returned = resp
                    return resp
                if failure.kind == FailureKind.EMPTY_CUT:
                    from app.agent import logger

                    logger.add_info("LLM").warning(
                        f"模型 {_cfg_model_label(cfg, model)} 的正文被 max_tokens="
                        f"{cfg.get('max_tokens', max_tokens)} 截断（输出 {failure.detail}）。"
                        "这不是重试能解决的问题：请调大「Provider 预设 → 模型」页该模型的"
                        "「最大输出 Token」，或缩短上下文"
                    )
                last_empty = resp
                if _should_retry_same(failure, attempt, allowance):
                    from app.agent import logger

                    logger.add_info("LLM").warning(
                        f"重试 [{attempt + 1}/{allowance}]：模型 {_cfg_model_label(cfg, model)}"
                        f"（{failure.describe()}）"
                    )
                    sleep_for = _retry_delay(failure, delay)
                    if sleep_for:
                        await asyncio.sleep(sleep_for)
                    continue
                if failure.deterministic:
                    from app.agent import logger

                    logger.add_info("LLM").error(
                        f"确定性失败（{failure.describe()}），停止整条回退链："
                        "换模型或原样重发都不会成功"
                    )
                    blocked_reason = failure.detail
                    returned = resp
                    return resp
                break
            if blocked_reason:
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

    判定口径已统一到 ``request_policy.classify_response``（它额外区分"被预算截断"
    "只有思考" "计费了却没产出"）；保留这个薄封装是因为"空/非空"这个二元问题在
    日志与第三方插件里仍然常用。
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
    budget: TurnBudget | None = None,
    input_tokens_estimate: int = 0,
):
    """按顺序尝试 config_chain 的流式 provider；仅在首个事件前出错才切换到下一个。

    **重试决策由失败分类决定**（与 ``chat_with_fallback`` 同一张表）：

    - 瞬态（429/5xx/超时/网关抖动/计费了却没产出）→ 同 provider 退避重试，听
      ``Retry-After``；用尽后换下一个 provider；
    - 确定性（参数/权限/模型不存在/上下文超长/余额/审核）→ 停整条链并交出真因；
    - 被 max_tokens 截断（``finish_reason=length`` 且无产出）→ 记一条可操作日志后
      换下一个 provider（回退器改不了预算，原样重发只会再烧一遍）。

    绝不重试"已产出内容"的情况：一旦有文本/工具调用/思考产出，重试就可能重复发送、
    重复执行工具。整条链都没产出时交出**最后一个真实错误**，否则调用方只看到
    "空回复"，429/余额不足/模型不存在这些真因彻底丢失。

    token 消耗：上游在流末尾回报的 usage 会累加，**本次流式请求结束时 info 一次**。
    带 ``usage_sink`` 时改为写进调用方的 dict（不在这里打日志）——流式的工具循环在
    调用方，由它把多轮汇总后只打一行。此时 ``usage_sink[SERVED_MODEL_KEY]`` 会被写成
    实际服务本轮的模型名，调用方据此拼日志/遥测，避免回退后仍记主模型名。

    ``budget`` 给整轮一个总请求次数与累计输入 token 上限（见 ``TurnBudget``）。
    """
    chain = list(config_chain or [])
    retries = max(0, int(max_empty_retries or 0))
    allowance = _attempt_allowance(retries, budget)
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
            failure: Failure | None = None
            for attempt in range(allowance + 1):
                if budget is not None:
                    allowed, why = budget.allow(
                        estimated_input=input_tokens_estimate,
                        reason=f"流式回退链 {_cfg_model_label(cfg, model)} 第 {attempt + 1} 次请求",
                    )
                    if not allowed:
                        from app.agent import logger

                        logger.add_info("LLM").warning(f"请求预算用尽，停止重试：{why}")
                        provider_error = provider_error or why
                        break
                started = False
                got_content = False
                attempt_usage: dict = {}
                finish_reason = ""
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
                        if ev.type == "done":
                            finish_reason = str(getattr(ev, "finish_reason", "") or "")
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
                            merge_usage(attempt_usage, event_usage)
                            # 带 sink 时**立刻**回写（不等 finally）：客户端提前 break
                            # 也能拿到已产出的 usage，调用方随后可以正常打一行汇总
                            if usage_sink is not None:
                                merge_usage(usage_sink, event_usage)
                        if ev.type == "text" and getattr(ev, "text", ""):
                            text_chars += len(ev.text)
                        started = True
                        yield ev
                except Exception as e:
                    from app.agent import logger
                    from app.agent.providers.base import format_llm_error

                    failure = classify_exception(e)
                    if budget is not None:
                        budget.charge(estimated_input=input_tokens_estimate, usage=attempt_usage)
                    logger.add_info("LLM").warning(
                        f"流式回退：模型 {_cfg_model_label(cfg, model)} 请求异常"
                        f"（{failure.describe()}）: {format_llm_error(e)}"
                    )
                    if started:
                        # 已经产出过事件：本轮由它服务，异常上抛但名字要留住
                        served_cfg = cfg
                        if usage_sink is not None:
                            usage_sink[SERVED_MODEL_KEY] = _cfg_model_label(cfg, model)
                        raise
                    provider_error = provider_error or format_llm_error(e)
                else:
                    if budget is not None:
                        budget.charge(estimated_input=input_tokens_estimate, usage=attempt_usage)
                    if not got_content and not provider_error:
                        failure = classify_response(
                            finish_reason=finish_reason,
                            usage=attempt_usage,
                            max_tokens=int(cfg.get("max_tokens", max_tokens) or 0),
                        )
                if got_content:
                    return
                if not provider_error and failure is None:
                    failure = classify_response(
                        finish_reason=finish_reason,
                        usage=attempt_usage,
                        max_tokens=int(cfg.get("max_tokens", max_tokens) or 0),
                    )
                if provider_error and failure is None:
                    failure = classify_error_text(provider_error)
                if failure is not None and failure.kind == FailureKind.EMPTY_CUT:
                    from app.agent import logger

                    logger.add_info("LLM").warning(
                        f"模型 {_cfg_model_label(cfg, model)} 的正文被 max_tokens="
                        f"{cfg.get('max_tokens', max_tokens)} 截断（{failure.detail}）。"
                        "这不是重试能解决的问题：请调大「Provider 预设 → 模型」页该模型的"
                        "「最大输出 Token」，或缩短上下文"
                    )
                if _should_retry_same(failure, attempt, allowance, provider_failed=bool(provider_error)):
                    from app.agent import logger

                    logger.add_info("LLM").warning(
                        f"流式重试 [{attempt + 1}/{allowance}]：模型 "
                        f"{_cfg_model_label(cfg, model)}（{failure.describe() if failure else ''}）"
                    )
                    sleep_for = _retry_delay(failure, delay)
                    if sleep_for:
                        await asyncio.sleep(sleep_for)
                    continue
                if failure is not None and failure.deterministic:
                    from app.agent import logger

                    logger.add_info("LLM").error(
                        f"确定性失败（{failure.describe()}），停止整条回退链："
                        "换模型或原样重发都不会成功"
                    )
                    last_error = provider_error or failure.detail
                    provider_error = ""
                    break
                if not (failure is not None and failure.switch_provider):
                    # 不该换 provider（例如"只有思考没有正文"）：留给调用方收敛
                    last_error = provider_error or (failure.detail if failure else "")
                    provider_error = ""
                    break
                # 该 provider 用尽重试：留给调用方的兜底文本，继续尝试下一个 provider
                break
            if provider_error:
                from app.agent import logger

                last_error = provider_error
                logger.add_info("LLM").warning(
                    f"流式回退：模型 {_cfg_model_label(cfg, model)} 失败，"
                    f"换下一个: {provider_error}"
                )
            if failure is not None and failure.deterministic:
                break
            if failure is not None and not failure.switch_provider:
                break
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