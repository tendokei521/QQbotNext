"""装配器共用的取值助手：把「会话历史 + 背景 + 记忆 + 技能」等原料取齐。"""

from __future__ import annotations

from typing import Any

from app.core.logger import logger


async def collect_history_materials(
    *,
    runtime: Any,
    session_mgr: Any,
    session_id: str,
    is_private: bool,
    config: Any,
    query_text: str,
    user_id: Any = None,
    bot: Any = None,
    compress: bool = True,
) -> dict:
    """取历史/记忆两类原料，口径与 chat 主路径一致。

    - 历史渲染用 ``group_context.format_history_for_llm``（与 chat 同一函数）；
    - ``compress=True`` 时超 ``history_rounds`` 的旧段压缩成摘要（与 chat 一致）；
      主动消息/定时任务此前不压缩，长会话会把上下文撑爆；
    - 记忆检索统一用 ``query_text``（调用方传"用户原始正文/触发语"），
      ``user_id`` 与 chat 一致地传会话对象 id（主动/定时场景可能为空）。
    """
    from app.llm.compress import maybe_compress_context
    from app.llm.group_context import format_history_for_llm

    rounds = int(config.get("history_rounds", 50) or 50)
    history = session_mgr.get_history(session_id, limit=rounds) if session_mgr else []
    history = format_history_for_llm(
        history,
        is_private=is_private,
        normalize_enhanced=bool(config.get("experimental_long_term_memory", False)),
        mask_nickname=True,
    )
    if compress:
        chain = runtime.provider_chain() if callable(getattr(runtime, "provider_chain", None)) else []
        history = await maybe_compress_context(chain, config, history, rounds)

    memory_text = ""
    memory = getattr(runtime, "memory", None)
    if memory is not None and memory.enabled():
        try:
            memory_text = await memory.recall_block_async(
                session_id, user_id if is_private else "", query_text, bot=bot
            )
        except Exception as e:  # noqa: BLE001 —— 记忆召回失败不应阻断本轮
            logger.add_info(f"#{getattr(runtime, 'bot_id', '?')}").debug(
                f"[Memory] 召回失败（已忽略）: {e}"
            )
            memory_text = ""
    return {"history": history, "memory_text": memory_text}
