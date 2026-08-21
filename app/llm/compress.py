"""上下文压缩策略（仿 AstrBot LLMSummaryCompressor，轮数阈值版）。

触发条件：
- 开启 `context_compress_enable`
- 会话历史条数 > `history_rounds * context_compress_threshold`（默认 75%）

行为：
- 把超出阈值的历史中“最早部分”交给 LLM 压缩成一段摘要；
- 最近 `history_rounds * context_compress_keep_ratio`（默认 25%）条消息保留原文；
- 最终返回：`system 摘要块 + 最近原文`，供本次请求使用。
- 压缩失败时静默回退为原始历史，不阻塞对话。
"""

from __future__ import annotations

from typing import Any

from app.llm import logger
from app.llm.providers import chat_with_fallback

DEFAULT_COMPRESS_PROMPT = """请把上面的历史对话压缩成一份简洁但完整的摘要，用于后续对话无缝续接：
1. 覆盖所有核心话题及最终结论/结果；
2. 高亮最近的主要关注点；
3. 如有工具调用、任务进度或待办，说明当前状态和下一步；
4. 保留用户的重要个人信息、偏好、称呼等关键事实；
5. 使用与对话相同的语言输出。
只输出摘要内容，不要输出额外解释。"""


def _ratio(config: dict, key: str, default: float) -> float:
    try:
        value = float(config.get(key, default))
    except (TypeError, ValueError):
        value = default
    if not 0 < value < 1:
        value = default
    return value


def should_compress(history: list[dict], history_rounds: int, config: dict) -> bool:
    """历史条数是否超过 history_rounds * threshold。"""
    if not config.get("context_compress_enable", True):
        return False
    if not history:
        return False
    threshold = _ratio(config, "context_compress_threshold", 0.75)
    limit = max(1, int(history_rounds * threshold))
    return len(history) > limit


def split_history(
    history: list[dict],
    keep_ratio: float = 0.25,
) -> tuple[list[dict], list[dict]]:
    """把历史切成「待压缩旧段」和「保留原文的最近段」。

    至少保留 1 条最近消息；keep_ratio 会被限制在 (0, 1)。
    """
    keep_ratio = min(max(float(keep_ratio), 0.05), 0.95)
    keep_count = max(1, int(len(history) * keep_ratio))
    if keep_count >= len(history):
        keep_count = max(1, len(history) - 1)
    return history[:-keep_count], history[-keep_count:]


def build_summary_messages(old_history: list[dict], prompt: str) -> list[dict]:
    """构造用于生成摘要的 messages。"""
    messages: list[dict] = [
        {"role": "system", "content": "你是一个对话摘要助手。请只输出摘要内容。"}
    ]
    messages.extend(old_history)
    if messages[-1]["role"] != "assistant":
        messages.append({"role": "assistant", "content": "Acknowledged."})
    messages.append({"role": "user", "content": prompt})
    return messages


async def maybe_compress_context(
    provider_chain: list[dict],
    config: dict,
    history: list[dict],
    history_rounds: int,
) -> list[dict]:
    """如果上下文过长，用 LLM 压缩旧段并保留最近 25% 原文。

    返回可直接传给 build_messages 的 history 列表；未触发或失败时原样返回。
    """
    if not should_compress(history, history_rounds, config):
        return history

    keep_ratio = _ratio(config, "context_compress_keep_ratio", 0.25)
    old_history, recent_history = split_history(history, keep_ratio)
    if not old_history:
        return history

    prompt = str(config.get("context_compress_prompt") or DEFAULT_COMPRESS_PROMPT).strip()
    summary_messages = build_summary_messages(old_history, prompt)

    model = config.get("model")
    temperature = float(config.get("temperature", 0.7))
    max_tokens = int(config.get("max_tokens", 1024))
    timeout = int(config.get("timeout", 60))

    try:
        resp = await chat_with_fallback(
            provider_chain,
            summary_messages,
            model=model,
            temperature=min(temperature, 0.5),
            max_tokens=max(512, max_tokens),
            timeout=timeout,
        )
        summary = (resp.text or "").strip()
    except Exception as e:  # noqa: BLE001
        logger.add_info("LLM").warning(f"上下文压缩调用失败，跳过压缩: {e}")
        return history

    if not summary:
        logger.add_info("LLM").warning("上下文压缩返回空摘要，跳过压缩")
        return history

    block = "【更早对话摘要】\n" + summary
    logger.add_info("LLM").info(
        f"上下文压缩: 旧 {len(old_history)} 条 -> 摘要，保留最近 {len(recent_history)} 条原文"
    )
    return [{"role": "system", "content": block}, *recent_history]
