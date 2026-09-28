"""上下文 token 预算与裁剪（对齐 AstrBot 的 ``ContextManager`` 思路）。

框架原本只有**按条数**的历史压缩（``history_rounds`` + LLM 摘要压缩），
没有「这一轮请求总共允许多大」的上限。长会话 + 大段群聊环境/记忆块时，
请求会一路涨到上游报错（400 context length）才发现。

这里给一个**可选**的预算闸门（``context_token_budget > 0`` 才生效）：
先估算本轮 messages 的 token 数，超预算就从**最旧的历史消息**开始丢，
system（人设/协议）与最后一条 user（本轮提问）永不丢。

估算用字符启发式（CJK 约 1 字≈1 token 起，保守按 ``chars_per_token`` 折算），
只用于「防爆」而非计费；上游回报的真实 usage 见 ``providers.log_token_usage``。
"""

from __future__ import annotations

from typing import Any

from app.llm import logger

DEFAULT_CHARS_PER_TOKEN = 2.0
#: 每条消息的固定开销（role/分隔符等），对齐主流 chat 模板的粗估
PER_MESSAGE_OVERHEAD = 4


def _cfg(config: Any, key: str, default: Any) -> Any:
    getter = getattr(config, "get", None)
    if not callable(getter):
        return default
    try:
        return getter(key, default)
    except Exception:  # noqa: BLE001 —— 配置对象异常不该影响裁剪判定
        return default


def budget(config: Any) -> int:
    """本轮的 token 预算；<=0 表示不启用（默认）。"""
    try:
        return int(_cfg(config, "context_token_budget", 0) or 0)
    except (TypeError, ValueError):
        return 0


def chars_per_token(config: Any) -> float:
    try:
        value = float(_cfg(config, "context_token_chars_per_token", DEFAULT_CHARS_PER_TOKEN))
    except (TypeError, ValueError):
        return DEFAULT_CHARS_PER_TOKEN
    return value if value > 0 else DEFAULT_CHARS_PER_TOKEN


def _content_chars(content: Any) -> int:
    """多模态 content 的字符数（只数文本块；图片按 URL/占位等长粗估）。"""
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    total += len(str(block.get("text", "")))
                else:
                    total += 256  # 图片/音频等按一个较大的常量粗估
            else:
                total += len(str(block))
        return total
    return len(str(content or ""))


def estimate_tokens(messages: list[dict] | None, *, per_token_chars: float = DEFAULT_CHARS_PER_TOKEN) -> int:
    """粗略估算 messages 的 token 数（防爆用，不用于计费）。"""
    ratio = per_token_chars if per_token_chars > 0 else DEFAULT_CHARS_PER_TOKEN
    total = 0
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        total += PER_MESSAGE_OVERHEAD
        total += _content_chars(msg.get("content"))
        for call in msg.get("tool_calls") or []:
            total += len(str(call)) if isinstance(call, dict) else len(str(call))
    return int(total / ratio) if ratio != 1 else total


def _is_protected(index: int, messages: list[dict]) -> bool:
    """受保护的消息：system（人设/协议）与最后一条 user（本轮提问）。"""
    msg = messages[index]
    if msg.get("role") == "system":
        return True
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    return index == last_user


def truncate(
    messages: list[dict],
    limit: int,
    *,
    per_token_chars: float = DEFAULT_CHARS_PER_TOKEN,
    bot_id: Any = None,
) -> tuple[list[dict], int]:
    """按 token 预算裁剪，返回 ``(新 messages, 丢掉的条数)``。

    从**最旧的历史消息**开始丢；system 与最后一条 user 永不丢。
    仍然超预算时不再硬切（说明预算小于必留内容），只留一条 warning。
    """
    messages = list(messages or [])
    if limit <= 0 or not messages:
        return messages, 0

    dropped = 0
    while estimate_tokens(messages, per_token_chars=per_token_chars) > limit:
        victim = next(
            (i for i in range(len(messages)) if not _is_protected(i, messages)),
            None,
        )
        if victim is None:
            logger.add_info(f"#{bot_id or '?'}").warning(
                f"[Context] token 预算 {limit} 小于必留内容（system + 本轮提问），本轮到 "
                f"{estimate_tokens(messages, per_token_chars=per_token_chars)} token 仍然超限"
            )
            break
        messages.pop(victim)
        dropped += 1
    return messages, dropped


def apply_budget(messages: list[dict], config: Any, *, bot_id: Any = None) -> list[dict]:
    """装配收口用：按配置裁剪（未启用或未超限时原样返回）。"""
    limit = budget(config)
    if limit <= 0:
        return messages
    ratio = chars_per_token(config)
    estimated = estimate_tokens(messages, per_token_chars=ratio)
    if estimated <= limit:
        return messages
    trimmed, dropped = truncate(messages, limit, per_token_chars=ratio, bot_id=bot_id)
    logger.add_info(f"#{bot_id or '?'}").info(
        f"[Context] token 预算裁剪：估算 {estimated} > 上限 {limit}，丢弃最旧 {dropped} 条历史"
    )
    return trimmed
