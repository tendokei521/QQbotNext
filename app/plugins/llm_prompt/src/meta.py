"""消息元信息渲染（精简版，只读 8 个模块键）。

渲染口径：

- `prompt_time_position`：off / head / tail；
- `user_message_format`：single_line / multi_line；
- `sender_label`：off / compact / verbose / masked；
- `quote_and_at`：off / mark / enrich（mark 只标"引用了某条"，enrich 才取正文与昵称）；
- `include_sent`：多行块里单独标正文；
- `auto_disambiguation` 只影响 L1 的说明段（见 `prompt.py`）。

框架只提供 `pre_request` 钩子与 `raw_user_text` 领域原语，渲染规则归本模块。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.agent import logger
from app.agent.context.group import UNRESOLVED_REPLY, extract_msg_text, has_real_content
from app.domain.trigger import raw_user_text as _raw_user_text


def _cfg(ctx, key: str, default: Any):
    from app.agent.policy import policy_config

    cfg = policy_config(getattr(ctx, "runtime", None), "meta")
    if cfg is not None and hasattr(cfg, "get"):
        try:
            return cfg.get(key, default)
        except Exception as e:  # noqa: BLE001 —— 单个键异常不该中断整轮渲染
            logger.debug(f"[Enhance] 读取配置 {key} 失败，使用默认值 {default!r}: {e}")
    return default


def _sender_label(ctx, qq: Any) -> str:
    """按 `sender_label` 档位渲染发送者；off 返回空串。"""
    style = str(_cfg(ctx, "sender_label", "compact") or "compact").lower()
    if style == "off" or qq in (None, ""):
        return ""
    if style == "masked":
        return f"用户<{qq}>"
    return str(qq)


async def collect_user_context(ctx) -> None:
    """收集本轮要注入的元信息（是否收集由 `sender_label` / `quote_and_at` 决定）。"""
    event = ctx.event
    info: dict[str, Any] = {
        "sender": None,
        "mentioned": [],
        "quote": None,
        "quote_sender": None,
        "quote_unresolved": False,
        "sent_text": _raw_user_text(event),
    }
    sender_style = str(_cfg(ctx, "sender_label", "compact") or "compact").lower()
    quote_mode = str(_cfg(ctx, "quote_and_at", "enrich") or "enrich").lower()

    if event.event_type == "message_group" and sender_style != "off":
        nickname = event.user.card or event.user.nickname or ""
        info["sender"] = f"{nickname}({event.user_id})" if nickname else str(event.user_id)

    if quote_mode != "off":
        info["mentioned"] = await _collect_at_info(ctx, fetch_nickname=quote_mode == "enrich")
        quote = await _collect_quote_info(ctx, enrich=quote_mode == "enrich")
        if quote:
            info["quote"] = quote["text"]
            info["quote_sender"] = quote["sender_nickname"]
            info["quote_unresolved"] = quote["unresolved"]

    ctx.state["user_context"] = info


async def format_user_context(ctx) -> None:
    """把元信息渲染进本轮 user 文本（与历史条目同一套字段语义）。"""
    from app.agent.assembly.prompt import user_message_format
    from app.agent.session.model import build_current_turn_text

    info = ctx.state.get("user_context")
    if not info:
        return

    event = ctx.event
    is_group = event.event_type == "message_group"
    cfg = getattr(getattr(ctx, "runtime", None), "config", None)
    single_line = user_message_format(cfg) == "single_line"
    time_position = str(_cfg(ctx, "prompt_time_position", "tail") or "tail").lower()
    sender_style = str(_cfg(ctx, "sender_label", "compact") or "compact").lower()

    head_lines: list[str] = []
    tail_lines: list[str] = []
    label = _sender_label(ctx, event.user_id if not is_group else None)

    # 时间行：off 不注入；head 放头、tail 放尾
    time_line = ""
    if time_position != "off":
        time_line = f"(时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')})"
        if time_position == "head":
            head_lines.append(time_line)

    # 私聊：verbose 档额外给出对方 QQ；群聊固定给群号（工具调用要用）
    if not is_group and sender_style == "verbose":
        user_id = getattr(event, "user_id", None)
        if user_id:
            head_lines.append(f"(QQ: {user_id})")
    if is_group:
        group_id = getattr(getattr(event, "group", None), "group_id", None)
        if group_id:
            head_lines.append(f"(当前群号: {group_id})")
        if sender_style != "off" and info.get("sender"):
            if single_line:
                # 单行形态：正文行前缀就是发送者（脱敏档只留 QQ）
                label = (
                    f"用户<{event.user_id}>"
                    if sender_style == "masked"
                    else str(info["sender"])
                )
            elif sender_style == "masked":
                head_lines.append(f"发送者：用户<{event.user_id}>")
            else:
                head_lines.append(f"发送者：{info['sender']}")

    if info.get("mentioned"):
        mentioned = "、".join(info["mentioned"])
        tail_lines.append(f"提到了(用户名)：{mentioned}")

    if info.get("quote"):
        if info.get("quote_sender"):
            tail_lines.append(f"引用了：{info['quote_sender']}发送的引用消息：“{info['quote']}”")
        else:
            tail_lines.append(f"引用了：{info['quote']}")
    if time_position == "tail" and time_line:
        tail_lines.append(time_line)

    sent_text = ctx.user_text.strip() or (info.get("sent_text") or "").strip()
    if not single_line and not bool(_cfg(ctx, "include_sent", True)):
        sent_text = ""

    formatted = build_current_turn_text(
        sent_text=sent_text,
        current_text=ctx.user_text,
        sender_label=label,
        head_lines=head_lines,
        tail_lines=tail_lines,
        sender_style="single" if single_line else "legacy",
        sent_style="compact",
    )
    ctx.user_text = formatted
    if len(formatted.splitlines()) > 1 or formatted != (info.get("sent_text") or "").strip():
        ctx.state["message_meta_injected"] = True


# ---------- 群成员昵称 / 引用辅助 ----------


async def _collect_at_info(ctx, *, fetch_nickname: bool) -> list[str]:
    event = ctx.event
    if not event.message:
        return []
    result: list[str] = []
    for seg in event.message:
        if seg.type != "at":
            continue
        qq = str(seg.data.get("qq", "") or "")
        if not qq:
            continue
        if qq in (str(event.self_id), str(getattr(event, "bot_id", "") or "")):
            continue
        if qq in ("all", "0"):
            result.append("全体成员")
            continue
        nickname = qq
        if fetch_nickname:
            fetched = await _fetch_group_member_nickname(ctx, qq)
            if fetched:
                nickname = fetched
        result.append(f"{nickname}({qq})")
    return result


async def _fetch_group_member_nickname(ctx, qq: str) -> str:
    """反查群成员昵称（与背景块渲染共用缓存）。"""
    from app.agent.context.nicknames import fetch_nickname

    event = ctx.event
    group_id = getattr(event.group, "group_id", None)
    if not group_id or not event.bot:
        return ""
    return await fetch_nickname(
        event.bot, group_id, qq, bot_id=str(getattr(event, "bot_id", "") or "")
    )


def _display_sender(ctx, sender: dict) -> str:
    """引用行的发送者显示名（句子型昵称 → ``用户<QQ>``）。"""
    from app.agent.context import display_names

    runtime = getattr(ctx, "runtime", None)
    event = getattr(ctx, "event", None)
    return display_names.display_for_sender(
        sender,
        bot_id=str(getattr(runtime, "bot_id", "") or getattr(event, "bot_id", "") or ""),
        group_id=str(getattr(getattr(event, "group", None), "group_id", "") or ""),
        runtime=runtime,
    )


async def _collect_quote_info(ctx, *, enrich: bool) -> dict | None:
    """引用信息：`enrich=True` 取回正文/发送者；否则只给一条可展开的缺口标记。"""
    event = ctx.event
    reply_id = None
    for seg in event.message or []:
        if seg.type == "reply":
            reply_id = str(seg.data.get("id", "") or "")
            break
    if not reply_id:
        return None
    if not enrich:
        return {
            "text": UNRESOLVED_REPLY.format(id=reply_id),
            "sender_nickname": "",
            "unresolved": True,
            "sender_id": "",
        }
    if not event.bot:
        return None
    try:
        resp = await event.bot.get_msg(reply_id)
        data = (resp or {}).get("data", {}) or {}
        sender = data.get("sender", {}) or {}
        sender_nickname = sender.get("card") or sender.get("nickname") or ""
        sender_id = sender.get("user_id", "")
        text = _segments_to_text(data.get("message"), message_id=reply_id)
        if not text or not has_real_content(text):
            text = UNRESOLVED_REPLY.format(id=reply_id)
        return {
            "text": text,
            "sender_nickname": _display_sender(ctx, sender) or sender_nickname or str(sender_id),
            "unresolved": False,
            "sender_id": sender_id,
        }
    except Exception as e:  # noqa: BLE001 —— 引用增强失败不阻断本轮
        logger.debug(f"[Enhance] 获取引用消息内容失败（reply_id={reply_id}）: {e}")
        return {
            "text": UNRESOLVED_REPLY.format(id=reply_id),
            "sender_nickname": "",
            "unresolved": True,
            "sender_id": "",
        }


def _segments_to_text(message, message_id: Any = None) -> str:
    """消息段 → 可读文本（@ 展开、非文本占位、缺口标记）。"""
    from app.domain.message import Message

    if isinstance(message, str):
        return message
    rendered = extract_msg_text(message, mark_unresolved=True, message_id=message_id)
    if has_real_content(rendered):
        return rendered
    msg = Message.from_onebot(message)
    fallback = "[" + ",".join(s.type for s in msg.segments) + "]" if msg.segments else ""
    return rendered or fallback
