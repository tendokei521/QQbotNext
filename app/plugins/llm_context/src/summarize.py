"""合并转发的异步摘要任务。

策略（用户确认）：
- 首次展开大转发时：全文直接给本轮（保证能答），同时**并发**起一次摘要请求；
- 摘要完成写入 `forward_cache`，后续轮次只给摘要 + `[forward_full:id]` 提示；
- 摘要失败不写缓存、不告警（debug 留痕），下一轮再展开时重来。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.agent import logger

#: 同进程内正在摘要的转发 id（防重复并发）
_INFLIGHT: set[str] = set()

#: 小转发不摘要：<=800 字 且 <=10 条
SMALL_CHARS = 800
SMALL_NODES = 10
#: 送进摘要请求的原文上限
MAX_INPUT_CHARS = 20000

SUMMARY_PROMPT = """把下面这段群聊合并转发压缩成 4 段，每段不超过 40 字：
1) 谁在聊（参与人）
2) 在聊什么（话题）
3) 关键信息/结论（保留 3~5 条可引用的原话片段）
4) 有没有待办/未解决
只输出这 4 段，不要额外解释。"""


def _render_nodes(nodes: list[dict]) -> str:
    lines: list[str] = []
    for index, node in enumerate(nodes, start=1):
        body = node.get("content") or node.get("message") or ""
        if isinstance(body, list):
            body = "".join(
                str((seg.get("data") or {}).get("text", "")) if isinstance(seg, dict) else ""
                for seg in body
            )
        lines.append(f"[node:{index}][sender:id={node.get('user_id', '')},name=\"{node.get('name', '')}\"]{body}")
    return "\n".join(lines)


async def _summarize(runtime: Any, forward_id: str, nodes: list[dict]) -> None:
    try:
        from app.agent.providers import chat_with_fallback

        chain = runtime.provider_chain() if callable(getattr(runtime, "provider_chain", None)) else []
        text = _render_nodes(nodes)[:MAX_INPUT_CHARS]
        resp = await chat_with_fallback(
            chain,
            [
                {"role": "system", "content": SUMMARY_PROMPT},
                {"role": "user", "content": text},
            ],
            temperature=0.2,
        )
        summary = str(getattr(resp, "text", "") or "").strip()
        if not summary:
            return
        from . import forward_cache

        forward_cache.put(
            getattr(runtime, "bot_id", ""),
            forward_id,
            summary=summary,
            nodes=len(nodes),
            chars=len(text),
        )
    except Exception as e:  # noqa: BLE001 —— 摘要失败不影响本轮
        logger.debug(f"[上下文] 合并转发摘要失败（forward_id={forward_id}）: {e}")
    finally:
        _INFLIGHT.discard(str(forward_id))


def maybe_schedule(runtime: Any, forward_id: str, nodes: list[dict], chars: int) -> None:
    """大转发才摘要；已有缓存/正在摘要/内容太小则跳过。"""
    from . import forward_cache

    key = str(forward_id)
    if key in _INFLIGHT or forward_cache.get(getattr(runtime, "bot_id", ""), key):
        return
    if chars <= SMALL_CHARS and len(nodes) <= SMALL_NODES:
        return
    try:
        _INFLIGHT.add(key)
        asyncio.create_task(_summarize(runtime, key, nodes))
    except RuntimeError:
        _INFLIGHT.discard(key)


def is_small(chars: int, nodes: int) -> bool:
    return chars <= SMALL_CHARS and nodes <= SMALL_NODES


__all__ = ["MAX_INPUT_CHARS", "SMALL_CHARS", "SMALL_NODES", "is_small", "maybe_schedule"]
