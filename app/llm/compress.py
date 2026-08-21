"""上下文压缩策略（仿 AstrBot LLMSummaryCompressor，仅压缩超出轮数限制的部分）。

逻辑：
- 会话历史超过 `history_rounds` 时，只把**超出** `history_rounds` 的最早部分交给 LLM 压缩成摘要；
- 最近 `history_rounds` 条消息保留原文，不压缩；
- 最终返回：`system 摘要块 + 最近 history_rounds 条原文`，供本次请求使用。
- 压缩失败时静默回退为原始历史，不阻塞对话。
"""

from __future__ import annotations

from app.llm import logger
from app.llm.providers import chat_with_fallback

DEFAULT_COMPRESS_PROMPT = """请把上面的历史对话压缩成一份简洁但完整的摘要，用于后续对话无缝续接：
1. 覆盖所有核心话题及最终结论/结果；
2. 高亮最近的主要关注点；
3. 如有工具调用、任务进度或待办，说明当前状态和下一步；
4. 保留用户的重要个人信息、偏好、称呼等关键事实；
5. 使用与对话相同的语言输出。
只输出摘要内容，不要输出额外解释。"""


def should_compress(history: list[dict], history_rounds: int, config: dict) -> bool:
    """历史是否超过了允许保留的轮数。"""
    if not config.get("context_compress_enable", True):
        return False
    if not history:
        return False
    return len(history) > int(history_rounds)


def split_history(
    history: list[dict],
    history_rounds: int,
) -> tuple[list[dict], list[dict]]:
    """切成「超出的旧段」和「保留原文的最近段」。

    只压缩超出 `history_rounds` 的部分；`history_rounds` 以内的消息全部保留。
    """
    keep_count = max(0, int(history_rounds))
    if keep_count <= 0:
        return history, []
    if keep_count >= len(history):
        return [], history
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
    """如果历史超过 history_rounds，把超出的旧段压缩成摘要，保留最近 history_rounds 条原文。

    返回可直接传给 build_messages 的 history 列表；未触发或失败时原样返回。
    """
    if not should_compress(history, history_rounds, config):
        return history

    old_history, recent_history = split_history(history, history_rounds)
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
        f"上下文压缩: 超出部分 {len(old_history)} 条 -> 摘要，保留最近 {len(recent_history)} 条原文"
    )
    return [{"role": "system", "content": block}, *recent_history]
