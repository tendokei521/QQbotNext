"""非流式一轮对话：**薄适配层**（真正实现在 `loop/turn.py::run_turn`）。

## 历史

这里曾经有一份 202 行的完整实现（取值 / 组装 / provider 回退 / 工具循环 /
思考溢出重试 / 空回复兜底 / 遥测 / 写历史），与 `chat.stream_response` 平行。
两份一旦漂移就会出现"流式修了、非流式没修"这类事故（例如 expand_image 的补图轮、
`_commit_history_enrichment` 的补全登记，都只在其中一条路径里修过）。

v3.1 起合并为**单一实现** `turn.run_turn`：

- 取值 → 组装 → `post_assemble` → 请求（含工具循环/重试/预算）→ 写历史 → 遥测
  → `turn_completed` 广播，全在 `run_turn` 里；
- 本文件只负责把 `(runtime, event, ctx)` 折成 `TurnRequest`，再把 `TurnResult`
  折回 ``str | None``（保持 `delivery/output.py` 等既有调用方的契约）。

新增非流式行为请改 `run_turn`，不要在这里加逻辑。
"""

from __future__ import annotations

from typing import Any

from app.agent import logger


async def generate_response(runtime: Any, event: Any, ctx: Any = None) -> str | None:
    """非流式一轮：返回回复正文（无回复时返回 None）。

    参数与旧接口完全一致，供 `delivery/output.py::BlockingOutput` 等既有调用方使用。
    """
    from app.agent.loop.turn import TurnRequest, run_turn
    from app.domain.trigger import raw_user_text

    message_type = getattr(event, "message_type", "")
    if message_type not in ("group", "private"):
        return None

    is_private = message_type == "private"
    if is_private:
        user_id = str(getattr(event, "user_id", "") or "")
        group_id = None
        session_id = f"private_{user_id}"
    else:
        group = getattr(event, "group", None)
        group_id = str(getattr(group, "group_id", "") or "")
        user_id = str(getattr(event, "user_id", "") or "")
        session_id = f"group_{group_id}"

    # 流水线已把正文归一化（去 @ / 合并抖动消息 / 截断），优先复用它；
    # 直调场景（没有 ctx）才从事件里抽原始文本。
    text = str(getattr(ctx, "user_text", "") or "").strip() if ctx is not None else ""
    if not text and event is not None:
        text = raw_user_text(event)
    if ctx is not None and getattr(ctx, "session_id", ""):
        session_id = str(ctx.session_id)

    result = await run_turn(
        runtime,
        TurnRequest(
            session_id=session_id,
            is_private=is_private,
            user_text=text,
            user_id=user_id,
            group_id=group_id,
            kind="chat",
            event=event,
            bot=getattr(event, "bot", None),
            stream=False,
            record_history=True,
        ),
        ctx=ctx,
    )

    if result.error:
        logger.add_info(f"#{getattr(runtime, 'bot_id', '?')}").warning(
            f"[LLM] 非流式生成失败: {result.error}"
        )
        return None
    if result.blocked:
        return None
    return result.text or None
