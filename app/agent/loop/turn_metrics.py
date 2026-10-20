"""一轮请求的**用量与预算**：token 估算、预算分配、请求日志、流式遥测。

## 本模块只负责

从「这轮大概要发多少」到「这轮实际花了多少 token」，以及把这些写成人可读的一行日志：
`_turn_budget` / `_input_tokens_estimate` / `_effective_max_tokens` / `_max_tool_rounds` /
`_usage_snapshot` / `_log_request_line` / `_record_stream_telemetry`。

## 为什么单独成文件

这些函数只跟数字打交道，和「这一轮说什么」无关，却挤在 2,192 行的 chat.py 里：
调一次 max_tokens 或改一行日志格式，都要在对话主流程中间翻找。搬出来后可以单独读、
单独测；`chat.py` 顶部重导出，对外 `chat._turn_budget` 等仍然可用。
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.agent import logger
from app.agent.assembly.token_budget import estimate_tokens
from app.agent.providers import SERVED_MODEL_KEY
from app.agent.providers.policy import TurnBudget, retry_profile_of


def _served_model_label(usage_sink: dict | None, fallback: Any) -> str:
    """本次请求实际服务的模型名（回退后由 provider 层写进 usage_sink）。

    取不到（调用方被插件整体替换、没走 provider 层）时退回配置里的主模型名。
    """
    served = str((usage_sink or {}).get(SERVED_MODEL_KEY) or "").strip()
    return served or str(fallback or "").strip()


DEFAULT_MAX_TOOL_ROUNDS = 5


def _max_tool_rounds(config, default: int = DEFAULT_MAX_TOOL_ROUNDS) -> int:
    """工具循环轮数上限（配置 ``max_tool_rounds``），流式与非流式共用同一值。

    以前非流式走 Provider 默认值、流式在 ``stream_response`` 里硬编码 ``range(5)``，
    两处不一致且不可配置。而“展开缺失的聊天环境 → 再判断是否还缺 → 再展开”本身就是
    多段链条，轮数被硬切就表现为模型“半途而废”直接作答。
    """
    try:
        value = int(config.get("max_tool_rounds", default) or default)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, 20))


DEFAULT_EMPTY_REPLY_RETRIES = 1


def _max_empty_retries(config, default: int = DEFAULT_EMPTY_REPLY_RETRIES) -> int:
    """空回复重试次数（配置 ``empty_reply_retries``），流式与非流式共用。

    偶现问题：请求成功结束却既无文本也无工具调用（网关提前断流 / 只回思考内容），
    表现就是用户收到兜底文本「抱歉，我暂时无法回答」。这里给一次确定性重试的机会，
    上限 3 次，避免真正故障时空跑 LLM 请求。0 = 关闭。
    """
    try:
        value = int(config.get("empty_reply_retries", default))
    except (TypeError, ValueError):
        return default
    return max(0, min(value, 3))


def _usage_snapshot(usage: dict | None) -> dict:
    """usage 累计快照（用来算"这一轮请求自己花了多少"）。"""
    if not usage:
        return {}
    return {k: v for k, v in usage.items() if isinstance(v, (int, float))}


def _usage_delta(usage: dict | None, before: dict, key: str) -> int:
    """本次请求的单项 token 增量（上游没报就返回 0，绝不编）。"""
    if not usage:
        return 0
    try:
        return max(0, int(usage.get(key, 0) or 0) - int(before.get(key, 0) or 0))
    except (TypeError, ValueError):
        return 0


def _log_request_line(
    runtime,
    *,
    session_id: str,
    round_index: int,
    served_model: str,
    max_tokens: int,
    max_tokens_src: str,
    input_tokens: int,
    output_tokens: int,
    input_estimate: int,
    finish_reason: str,
    text_chars: int,
    reasoning_chars: int,
    tools: int,
    error: str,
    budget: TurnBudget,
) -> None:
    """**每一次**真实请求打一行结构化记录（流式路径）。

    为什么值得单独一行：出问题时大家看的就是日志，而此前一次请求的信息散在四五处
    （模型名在一行、token 在另一行、max_tokens 只在开头可能出现、重试次数只体现在
    多条"重试"警告里）。这里一行给全，并且用固定字段名 ``key=value`` 便于 grep：

        model= in= out= max_tokens=(来源) finish= text= reasoning= tools= retry=n/m

    上游没回报 token 时写 ``in=?`` ``out=?`` 而不是 0 —— 0 会被误读成"没花 token"。

    **打 DEBUG 而不是 INFO**：这是逐请求的内部遥测，一轮对话里会出现好几次，
    对"机器人回了什么"没有信息量，却会把 user.log（用户简洁日志）刷成一屏内部字段。
    排查时打开 debug.log 就能看到完整字段，一行都不少。
    """
    from app.agent.providers.base import has_token_usage

    reported = has_token_usage(
        {"prompt_tokens": input_tokens, "completion_tokens": output_tokens}
    )
    in_txt = str(input_tokens) if reported else f"?≈{input_estimate}"
    out_txt = str(output_tokens) if reported else "?"
    parts = [
        f"model={served_model or '?'}",
        f"round={round_index + 1}",
        f"in={in_txt}",
        f"out={out_txt}",
        f"max_tokens={max_tokens}({max_tokens_src})",
        f"finish={finish_reason or '-'}",
        f"text={text_chars}字",
    ]
    if reasoning_chars:
        parts.append(f"reasoning={reasoning_chars}字")
    if tools:
        parts.append(f"tools={tools}")
    parts.append(f"budget={budget.requests}/{budget.max_requests}")
    log = logger.add_info(f"#{getattr(runtime, 'bot_id', '?')}")
    if error:
        # 出错才升级到 WARNING：这时的字段是排查入口，不能只留在 debug.log
        parts.append(f"error={error}")
        log.warning("[LLM] 请求 " + " ".join(parts) + f" -> {session_id}")
    else:
        log.debug("[LLM] 请求 " + " ".join(parts) + f" -> {session_id}")


def _turn_budget(config) -> TurnBudget:
    """本轮请求的总预算闸门（请求次数 + 累计输入 token）。

    为什么需要它：重试散在三层（HTTP 适配器 ``retry_attempts``、回退层空回复重试、
    调用方收敛重试），每层单独看都"只多一次"，乘起来却是十几次，而且**每次都重发
    全量上下文**（线上为此把 16.7 万 token 发了三遍还没出头）。这里把总账收在一处：
    档位决定最多几次请求、累计输入 token 到线就停，停了如实写日志。

    注意它只是**闸门**，不改变重试策略本身（谁该重试仍由失败分类决定）。
    """
    try:
        return TurnBudget.from_config(config)
    except Exception as e:  # noqa: BLE001 —— 预算读不出来不该拖垮整轮回复
        logger.add_info("LLM").warning(f"[Budget] 预算配置解析失败，改用默认档位: {e}")
        return TurnBudget(profile=retry_profile_of(None))


def _input_tokens_estimate(messages: list[dict]) -> int:
    """这份上下文大概多少输入 token（用于预算累计；上游回了真实 usage 就以真实值为准）。"""
    try:
        return int(estimate_tokens(messages))
    except Exception:  # noqa: BLE001 —— 估算失败就当 0，闸门退化为只按次数算
        return 0


DEFAULT_REASONING_OVERFLOW_RETRIES = 1


REASONING_OVERFLOW_NUDGE = (
    "【系统提醒】上一轮你的思考把输出预算用满了，正文还没开始就被截断，"
    "对方什么都没收到。这一轮不要复述思考过程，直接给最终回答；"
    "如果要看图，就只说你看到的关键内容，别长篇推理。"
)


def _reasoning_overflow_retries(config, default: int = DEFAULT_REASONING_OVERFLOW_RETRIES) -> int:
    """思考吃满 ``max_tokens`` 时的收敛重试次数（配置 ``reasoning_overflow_retries``）。

    思考模型（deepseek-v4-flash / reasoner 系）把全部输出预算花在 ``reasoning_content``
    里时，正文一个字节都没有。同一条 prompt 原样重试必然同样打满（纯烧钱），所以改成
    追加一次「直接作答」提醒再要一枪。0 = 关闭。
    """
    try:
        value = int(config.get("reasoning_overflow_retries", default))
    except (TypeError, ValueError):
        return default
    return max(0, min(value, 2))


def _effective_max_tokens(provider_chain, fallback) -> tuple[int, str]:
    """本次请求**真正生效**的 ``max_tokens``，以及它来自哪一层配置。

    取值优先级（高 → 低）：

    1. ``provider_models.<id>.config_json.max_tokens``（模型实例，WebUI「Provider 预设 → 模型」页）
    2. ``provider_presets.<id>.config_json.max_tokens``（连接预设，同一页的预设表单）
    3. Agent 配置 ``max_tokens``（WebUI「Agent → 最大输出 Tokens」）

    前两层在 ``AgentRuntime._config_for_model_id`` 里合并进链上每个 cfg，而
    ``chat_with_fallback`` 用 ``cfg.get("max_tokens", max_tokens)`` 取值 —— 所以
    **链上配置赢**，Agent 页那个数字只在链上没写时才生效。

    来源由 ``_config_for_model_id`` 写进 ``_max_tokens_origin``（合并后的 cfg 恒带该键，
    光看值是分辨不出三层的）；它是**纯来源名**，调用方自己拼 "来自{来源}"。
    拿不到标记时才退回「链上 = provider、否则 agent」的粗判。
    """
    for cfg in provider_chain or []:
        value = (cfg or {}).get("max_tokens")
        if value in (None, ""):
            continue
        try:
            origin = str((cfg or {}).get("_max_tokens_origin") or "provider 模型/预设")
            return int(value), origin
        except (TypeError, ValueError):
            break
    try:
        return int(fallback or 0), "agent 配置"
    except (TypeError, ValueError):
        return 0, "agent 配置"


_MAX_TOKENS_WARNED: set[tuple] = set()


def _max_tokens_override_note(runtime, effective: int, origin: str) -> str:
    """Agent 页显式设了 max_tokens 却被 provider 层覆盖时，给一句可操作的提示。

    这是「配置假象」的显式化：用户以为改的是 Agent 页，实际生效的是「Provider 预设 →
    模型」里那个值，而此前没有任何地方告诉他。只在**用户显式设过**且值确实不同时提示，
    否则只是默认值差异，属于噪音。
    """
    if origin == "agent 配置":
        return ""
    config = getattr(runtime, "config", None)
    checker = getattr(config, "has_explicit", None)
    if not callable(checker):
        return ""
    try:
        if not checker("max_tokens"):
            return ""
        agent_value = int(config.get("max_tokens", 0) or 0)
    except Exception:  # noqa: BLE001 —— 诊断辅助，失败不影响主流程
        return ""
    if not agent_value or agent_value == effective:
        return ""
    marker = (getattr(runtime, "bot_id", ""), agent_value, effective, origin)
    if marker in _MAX_TOKENS_WARNED:
        return ""
    _MAX_TOKENS_WARNED.add(marker)
    return (
        f"Agent 页的 max_tokens={agent_value} 未生效，本次实际用 {effective}"
        f"（来自{origin}）—— 要改「Provider 预设 → 模型」里该模型的「最大输出 Tokens」"
    )


def _record_stream_telemetry(
    runtime,
    session_id: str,
    provider_chain: list[dict],
    model: str,
    messages: list[dict],
    full_text_parts: list[str],
    tool_results: list[dict],
    stream_start: float,
    success: bool,
    error_text: str = "",
    usage: dict | None = None,
) -> None:
    """流式生成的统一遥测落点。"""
    telemetry = getattr(runtime, "telemetry", None)
    if telemetry is None:
        return
    full_text = "".join(full_text_parts)
    first_cfg = provider_chain[0] if provider_chain else {}
    tokens = usage or {}
    telemetry.record_call_simple(
        bot_id=runtime.bot_id,
        session_id=session_id,
        provider=str(first_cfg.get("provider", "openai") or "openai"),
        # 实际服务者（回退后不是 chain[0]）：与 token 消耗那一行同源，避免遥测也记错模型
        model=_served_model_label(tokens, first_cfg.get("model", model)),
        stream=True,
        success=success,
        latency_ms=(time.monotonic() - stream_start) * 1000,
        input_tokens=int(tokens.get("prompt_tokens") or tokens.get("input_tokens") or 0),
        output_tokens=int(tokens.get("completion_tokens") or tokens.get("output_tokens") or 0),
        message_count=len(messages),
        tool_calls=len(tool_results),
        characters=len(full_text),
        error=error_text,
    )


def _log_debug_prompt(runtime, session_id: str, messages: list[dict], debug_enabled: bool = False) -> None:
    """调试开关开启时，打印本轮完整 prompt。"""
    if not debug_enabled:
        return
    try:
        text = json.dumps(messages, ensure_ascii=False, indent=2)
    except Exception:
        text = str(messages)
    logger.add_info(f"#{runtime.bot_id}").info(f"[Prompt] {session_id}\n{text}")


def _history_meta_flags(runtime) -> dict:
    """计算历史渲染标志：是否归一化单行、是否脱敏昵称。

    mask_nickname 恒为 True：句子型/超长昵称一律脱敏，避免昵称内容泄漏进 LLM 上下文。
    """
    return {
        "normalize_enhanced": bool(runtime.config.get("experimental_long_term_memory", False)),
        "mask_nickname": True,
    }
