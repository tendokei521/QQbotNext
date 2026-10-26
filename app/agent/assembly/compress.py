"""上下文压缩策略（仿 AstrBot LLMSummaryCompressor，仅压缩超出轮数限制的部分）。

逻辑：
- 会话历史超过 `history_rounds` 时，只把**超出** `history_rounds` 的最早部分交给 LLM 压缩成摘要；
- 最近 `history_rounds` 条消息保留原文，不压缩；
- 最终返回：`system 摘要块 + 最近 history_rounds 条原文`，供本次请求使用。
- 压缩失败时静默回退为原始历史，不阻塞对话。
"""

from __future__ import annotations

from typing import Any

from app.agent import logger
from app.agent.providers import chat_with_fallback

DEFAULT_COMPRESS_PROMPT = """请把上面的历史对话压缩成一份简洁但完整的摘要，用于后续对话无缝续接：
1. 覆盖所有核心话题及最终结论/结果；
2. 高亮最近的主要关注点；
3. 如有工具调用、任务进度或待办，说明当前状态和下一步；
4. 保留用户的重要个人信息、偏好、称呼等关键事实；
5. 使用与对话相同的语言输出。
只输出摘要内容，不要输出额外解释。"""


def should_compress(
    history: list[dict],
    history_rounds: int,
    config: dict,
    *,
    pressure: bool = False,
) -> bool:
    """是否该压缩。

    两种触发（Q1）：``pressure=True`` 表示调用方按 token 压力判定已越线；
    否则回退到"超出 history_rounds 条"的旧口径（``context_compress_trigger_ratio<=0``
    时永远走这条，行为与改造前一致）。
    """
    if not config.get("context_compress_enable", True):
        return False
    if not history:
        return False
    if pressure:
        return True
    return len(history) > int(history_rounds)


def split_history(
    history: list[dict],
    history_rounds: int,
) -> tuple[list[dict], list[dict]]:
    """切成「超出的旧段」和「保留原文的最近段」。

    只压缩超出 `history_rounds` 的部分；`history_rounds` 以内的消息全部保留。

    **绝不切开工具配对**（P5）：切点若落在 `assistant(tool_calls)` 与紧随的
    `role:tool` 之间，保留段就会以"没有调用方的工具结果"开头、摘要段以"没有结果的
    工具调用"结尾，上游必然 400。这里用 `pairing.legal_cut` 在必要时把切点往前挪
    （最多挪到 0 = 不压缩）——宁可少压一轮，也不发出非法请求。
    """
    from app.agent.assembly.pairing import legal_cut

    keep_count = max(0, int(history_rounds))
    if keep_count <= 0:
        return history, []
    if keep_count >= len(history):
        return [], history
    cut = legal_cut(history, keep_count)
    return history[:cut], history[cut:]


def _text_only(message: dict) -> dict:
    """多模态消息 → 纯文本消息（摘要请求不传图块）。

    历史里可能带着会话图片的 ``image_url`` 块；摘要模型不一定声明 image 模态，
    原样发过去会被网关拒（整段压缩随之失败）。摘要只需要文字，这里先剥成文本。
    """
    content = message.get("content")
    if not isinstance(content, list):
        return message
    texts = [
        str(part.get("text", "") or "")
        for part in content
        if isinstance(part, dict) and part.get("type") == "text"
    ]
    return {**message, "content": "\n".join(t for t in texts if t)}


def build_summary_messages(old_history: list[dict], prompt: str) -> list[dict]:
    """构造用于生成摘要的 messages。"""
    messages: list[dict] = [
        {"role": "system", "content": "你是一个对话摘要助手。请只输出摘要内容。"}
    ]
    messages.extend(_text_only(m) for m in old_history if isinstance(m, dict))
    if messages[-1]["role"] != "assistant":
        messages.append({"role": "assistant", "content": "Acknowledged."})
    messages.append({"role": "user", "content": prompt})
    return messages


async def maybe_compress_context(
    provider_chain: list[dict],
    config: dict,
    history: list[dict],
    history_rounds: int,
    *,
    bot_id: str = "",
    session_id: str = "",
    pressure: bool = False,
    log: Any = None,
) -> list[dict]:
    """按压力/条数决定是否压缩，并交给配置选定的后端处理。

    - ``pressure=True``：调用方已按 token 压力判定越线（Q1）；
    - ``log``：`session_log.SessionLog`，用来记 `compaction/start|summary|end` 三段事件
      （崩溃时未闭合的 start 可被检出；summary 里带被替换条数与摘要文本）。

    返回可直接传给 build_messages 的 history 列表；未触发或失败时原样返回。
    """
    from app.agent.assembly.compaction import CompactionPlan, choose_backend
    from app.agent.assembly.context_budget import keep_count_for
    from app.agent.session.session_log import (
        EVENT_COMPACTION_END,
        EVENT_COMPACTION_START,
        EVENT_COMPACTION_SUMMARY,
        safe_append,
    )

    if not should_compress(history, history_rounds, config, pressure=pressure):
        return history

    keep_target = (
        keep_count_for(history, config, history_rounds) if pressure else int(history_rounds)
    )
    old_history, recent_history = split_history(history, keep_target)
    if not old_history:
        return history

    start_seq = safe_append(
        log, EVENT_COMPACTION_START,
        reason="pressure" if pressure else "count",
        total=len(history), keep=len(recent_history), replaced=len(old_history),
    )
    backend = choose_backend(config)
    plan = CompactionPlan(
        trigger_ratio=float(config.get("context_compress_trigger_ratio", 0) or 0),
        keep_ratio=float(config.get("context_compress_keep_ratio", 0) or 0),
        keep_tail=keep_target,
        reason="pressure" if pressure else "count",
    )
    result = await backend.compact(
        old_history, recent_history,
        plan=plan, provider_chain=provider_chain, config=config,
    )
    if result.compacted:
        safe_append(
            log, EVENT_COMPACTION_SUMMARY,
            backend=backend.name, summary=result.summary,
            pruned=result.pruned, replaced=result.replaced,
            source_event_seqs=[start_seq] if start_seq else [],
        )
    safe_append(
        log, EVENT_COMPACTION_END,
        backend=backend.name, compacted=bool(result.compacted),
        messages_before=len(history), messages_after=len(result.history),
    )
    logger.add_info("LLM").info(
        f"上下文压缩({backend.name}): {len(old_history)} 条 -> "
        f"{'摘要/裁剪' if result.compacted else '未压缩'}，保留最近 {len(recent_history)} 条原文"
    )
    return result.history


async def compress_history(
    provider_chain: list[dict],
    config: dict,
    old_history: list[dict],
    *,
    bot_id: str = "",
    session_id: str = "",
) -> str:
    """把旧段交给摘要模型，返回摘要文本（失败/空返回空串，由后端回退）。"""
    prompt = str(config.get("context_compress_prompt") or DEFAULT_COMPRESS_PROMPT).strip()
    summary_messages = build_summary_messages(old_history, prompt)

    # 模型参数只认 provider chain（agent 配置已无这三个键）
    _served = provider_chain[0] if provider_chain else {}
    model = _served.get("model")
    temperature = float(_served.get("temperature") or 0.7)
    max_tokens = int(_served.get("max_tokens") or 1024)
    timeout = int(_served.get("timeout") or 60)

    try:
        from app.agent.usage.context import usage_scope

        # 压缩摘要是**内部维护调用**：单独记 source=compress，
        # 否则会被算进"对话用量"，让 chat 的 token 虚高。
        with usage_scope(bot_id=bot_id, source="compress", session_id=session_id):
            resp = await chat_with_fallback(
                provider_chain,
                summary_messages,
                model=model,
                temperature=min(temperature, 0.5),
                max_tokens=max(512, max_tokens),
                timeout=timeout,
            )
        return (resp.text or "").strip()
    except Exception as e:  # noqa: BLE001
        logger.add_info("LLM").warning(f"上下文压缩调用失败，跳过压缩: {e}")
        return ""