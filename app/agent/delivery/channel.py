"""交付通道：**唯一的发送出口**。

## 为什么需要它

同一段"解析 session 前缀 → 选 OneBot API → 发出去"的代码此前在 5 处重复：

```
app/agent/loop/pipeline.py:520      （chat 路径）
app/agent/proactivity/manager.py:388（主动消息）
app/agent/proactivity/stream.py:92  （主动流式）
app/agent/proactivity/scheduler.py:354 / :397（定时任务）
```

后果不只是重复：**主动消息与定时任务这两条路径完全没有触发
`pre_send` / `post_send` 钩子，也不解析 `[reply]` / `[@qq]` 输出指令**，
同一条 LLM 回复走不同路径会有不同表现。

## 契约

``send_item`` 只做三件事，且顺序固定：

1. 解析会话 id（`domain.session.parse_session_id`）；
2. 需要时解析输出指令（`[reply]` / `[@qq]`）——**默认开启，无条件**；
3. 经 `bot.send_group_msg` / `send_private_msg` 发出。

一定走 ``bot`` 而不是 ``bot._direct_send``：前者会经过出站节点链 +
`before_send_hook` / `send_hook` / `api_hook`，后者是链末端裸发送。

``hooks=True`` 时额外触发 LLM 流水线的 ``pre_send`` / ``post_send``
（这两条路径此前完全没有），失败只留痕不阻断发送。
"""

from __future__ import annotations

from typing import Any

from app.core.logger import logger
from app.domain.message import Message
from app.domain.session import parse_session_id


async def send_item(
    bot: Any,
    session_id: str,
    message: Any,
    *,
    runtime: Any = None,
    ctx: Any = None,
    parse_directives: bool = True,
    max_at: int = 3,
    event: Any = None,
    hooks: bool = True,
) -> dict | None:
    """按会话发送一条消息，返回 OneBot 响应（失败为 None）。

    Args:
        bot: ``IBot``（必须是 BotConnection 才能走出站链与钩子）。
        session_id: ``group_<id>`` / ``private_<id>``。
        message: ``Message`` / ``MessageSegment`` / 纯文本 / 消息段数组。
        runtime: 仅用于 ``hooks=True`` 时执行 LLM 流水线钩子；可为 None。
        ctx: LLM 上下文（钩子参数）；可为 None（自动合成一个最小上下文）。
        parse_directives: 是否解析 ``[reply]`` / ``[@qq]`` 前缀指令（默认 True）。
        max_at: 指令里允许的 @ 个数上限。
        event: 触发事件（指令里的 ``[reply]`` 需要它的 ``message_id``）。
        hooks: 是否触发 ``pre_send`` / ``post_send``。

    Returns:
        OneBot 响应字典；被出站节点拦截或发送失败时返回 None。

    Raises:
        SessionIdError: ``session_id`` 无法解析。
    """
    is_group, target = parse_session_id(session_id)

    outgoing = _to_message(message)
    if parse_directives and outgoing is not None:
        outgoing = _apply_directives(outgoing, event=event, is_group=is_group, max_at=max_at)
    if outgoing is None:
        return None

    if hooks and runtime is not None:
        if not await _run_hook(runtime, "pre_send", ctx, outgoing):
            return None

    if bot is None:
        logger.add_info("Delivery").warning(f"[Send] 无可用 Bot，发送被跳过: {session_id}")
        return None

    try:
        if is_group:
            response = await bot.send_group_msg(int(target), outgoing)
        else:
            response = await bot.send_private_msg(int(target), outgoing)
    except Exception as e:  # noqa: BLE001 —— 发送失败应当回传为 None，不炸调用方
        logger.add_info("Delivery").error(f"[Send] 发送失败 {session_id}: {e}")
        return None

    if hooks and runtime is not None:
        await _run_hook(runtime, "post_send", ctx, outgoing)
    return response


def _to_message(message: Any) -> Message | None:
    """把任意可发送形态归一成 ``Message``；空内容返回 None（不发空消息）。"""
    if isinstance(message, Message):
        return message if message.segments else None
    if isinstance(message, str):
        text = message.strip()
        return Message.from_text(text) if text else None
    if isinstance(message, (list, tuple)):
        msg = Message(list(message))
        return msg if msg.segments else None
    if message is None:
        return None
    text = str(message).strip()
    return Message.from_text(text) if text else None


def _apply_directives(msg: Message, *, event: Any, is_group: bool, max_at: int) -> Message:
    """解析并剥离正文开头的 ``[reply]`` / ``[@qq]``，转成真正的消息段。"""
    text = msg.text
    if not text:
        return msg
    from app.agent.delivery.directives import (
        assemble_message,
        parse_outbound_directives,
    )

    directives, clean = parse_outbound_directives(text, max_at=max_at)
    if not directives:
        # 无指令也要防御性剥离（非开头的残留标记不能漏给用户）
        if clean != text:
            return Message.from_text(clean)
        return msg
    return assemble_message(clean, directives, event, is_group=is_group)


async def _run_hook(runtime: Any, stage: str, ctx: Any, msg: Message) -> bool:
    """执行 ``pre_send`` / ``post_send``；异常只留痕，不阻断发送。"""
    try:
        from app.agent.hooks import run_stage

        if ctx is None:
            from app.agent.loop.context import LlmContext

            ctx = LlmContext(
                event=None,
                runtime=runtime,
                bot=None,
                session_id="",
                user_text="",
            )
        return bool(await run_stage(runtime, stage, ctx, msg))
    except Exception as e:  # noqa: BLE001 —— 钩子异常不该让消息发不出去
        logger.add_info(f"#{getattr(runtime, 'bot_id', '?')}").debug(
            f"[Send] {stage} 钩子执行异常（已忽略）: {e}"
        )
        return True
