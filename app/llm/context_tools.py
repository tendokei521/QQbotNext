"""按需展开上下文的系统级工具（``expand_context``）。

定位：把渲染层标出来的「骨架」补成「血肉」。渲染层已经尽力预展开（@ 昵称、触发消息的
引用），但仍有三类内容只能按需取：更早/被引用的消息正文、合并转发内容、某个 QQ 是谁。

设计要点（照抄 ``session_tools`` 的既有约定）：
- **零参数可用**：不传参就展开"本轮触发消息里"的 @ 对象与被引用消息，模型不需要先知道 id；
- **支持批量**：``users`` / ``messages`` 可一次传多个，内部并发执行——一次展开多个 id 是
  常态，串行会把墙钟时间叠加到超时；
- **自解释返回**：每条结果都带 ``relation``（"当前消息 @ 的对象" / "当前消息引用的消息"），
  模型才知道这份数据是干什么用的；
- **只承诺做得到的事**：图片/语音等无法转成文本的内容不在此工具的能力范围内（渲染层也
  保持 ``[图片]`` 纯占位），避免提示词承诺一个不存在的展开动作。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.logger import logger
from app.llm import nicknames
from app.llm.group_context import extract_msg_text
from app.llm.tool import ToolSpec

# 单条内容默认截断长度（工具结果外层还有 TOOL_RESULT_MAX=2000 的总截断，
# 这里先按条截断，避免"展开 5 条"时最后几条被外层整体砍掉）
DEFAULT_ITEM_CHARS = 500
MAX_ITEMS = 10
# 合并转发最多摘几条
MAX_FORWARD_NODES = 10


def _as_list(value: Any) -> list:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _int_ids(value: Any) -> list[str]:
    """归一化为纯数字 id 列表（去重保序）；非法项丢弃。"""
    out: list[str] = []
    for raw in _as_list(value):
        try:
            text = str(int(str(raw).strip()))
        except (TypeError, ValueError):
            continue
        if text not in out:
            out.append(text)
    return out


def _str_ids(value: Any) -> list[str]:
    out: list[str] = []
    for raw in _as_list(value):
        text = str(raw or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _limit(args: dict) -> int:
    try:
        value = int(args.get("limit") or DEFAULT_ITEM_CHARS)
    except (TypeError, ValueError):
        value = DEFAULT_ITEM_CHARS
    return max(50, min(value, 2000))


def _segments(message: Any) -> list:
    if isinstance(message, list):
        return message
    return []


def _seg_field(seg: Any, key: str, default: Any = "") -> Any:
    if isinstance(seg, dict):
        return (seg.get("data") or {}).get(key, default)
    return (getattr(seg, "data", {}) or {}).get(key, default)


def _seg_type(seg: Any) -> str:
    return seg.get("type", "") if isinstance(seg, dict) else getattr(seg, "type", "")


def derive_targets(event: Any, self_ids: set[str]) -> tuple[list[str], list[str]]:
    """从本轮触发消息里推导要展开的对象；返回 ``(users, messages)``。

    - ``users``：消息里 @ 的其他人（跳过自己/全体）；
    - ``messages``：被引用的消息 id、以及合并转发的 id。
    """
    users: list[str] = []
    messages: list[str] = []
    for seg in _segments(getattr(event, "message", None)):
        stype = _seg_type(seg)
        if stype == "at":
            qq = str(_seg_field(seg, "qq"))
            if qq and qq not in ("all", "0") and qq not in self_ids and qq not in users:
                users.append(qq)
        elif stype in ("reply", "forward"):
            mid = str(_seg_field(seg, "id", "") or _seg_field(seg, "message_id", ""))
            if mid and mid not in messages:
                messages.append(mid)
    return users, messages


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


async def _describe_user(bot: Any, group_id: Any, qq: str, relation: str, *, bot_id: Any = "") -> str:
    """把一个 QQ 展开成"是谁"（群聊优先取群名片/角色）。"""
    try:
        if group_id not in (None, ""):
            resp = await bot.get_group_member_info(group_id=int(group_id), user_id=int(qq))
            data = (resp or {}).get("data") if isinstance(resp, dict) else None
            data = data if isinstance(data, dict) else {}
            nickname = str(data.get("card") or data.get("nickname") or "")
            # 共享昵称缓存：本次展开过的昵称，后续渲染背景块直接命中
            nicknames.remember(bot_id, group_id, qq, str(data.get("card") or data.get("nickname") or ""))
            if not data:
                return ""
            bits = [f"昵称：{nickname or '未知'}"]
            if data.get("card"):
                bits.append(f"群名片：{data.get('card')}")
            if data.get("role"):
                bits.append(f"角色：{data.get('role')}")
            if data.get("level"):
                bits.append(f"等级：{data.get('level')}")
            if data.get("title"):
                bits.append(f"头衔：{data.get('title')}")
            return f"【用户 {qq}】{'；'.join(bits)}；relation：{relation}"
        resp = await bot.get_stranger_info(user_id=int(qq))
        data = (resp or {}).get("data") if isinstance(resp, dict) else None
        data = data if isinstance(data, dict) else {}
        if not data:
            return ""
        nickname = str(data.get("nickname") or "") or "未知"
        return f"【用户 {qq}】昵称：{nickname}；relation：{relation}"
    except Exception as e:
        logger.debug(f"[ExpandContext] 展开用户 {qq} 失败（已忽略）: {e}")
        return ""


async def _describe_message(bot: Any, mid: str, limit: int, relation: str) -> str:
    """把一条消息 id 展开成"谁说的、说了什么"（含合并转发的一层内容）。"""
    try:
        message_id: Any = int(mid) if str(mid).lstrip("-").isdigit() else mid
        resp = await bot.get_msg(message_id=message_id)
        data = (resp or {}).get("data") if isinstance(resp, dict) else None
        if not isinstance(data, dict) or not data:
            return ""
        sender = data.get("sender") or {}
        label = str(sender.get("card") or sender.get("nickname") or sender.get("user_id") or "未知")
        sender_id = str(sender.get("user_id") or "")
        text = extract_msg_text(data.get("message"))
        bits = [f"来自 {label}" + (f"({sender_id})" if sender_id else "")]
        if text:
            bits.append(_truncate(text, limit))
        # 合并转发：再展开一层（这是一层深度的"引用链"，不递归，避免无界展开）
        forward_id = ""
        for seg in _segments(data.get("message")):
            if _seg_type(seg) == "forward":
                forward_id = str(_seg_field(seg, "id", "") or "")
                break
        if forward_id:
            nodes = await _describe_forward(bot, forward_id, limit)
            if nodes:
                bits.append("合并转发内容：" + nodes)
        return f"【消息 {mid}】{'；'.join(bits)}；relation：{relation}"
    except Exception as e:
        logger.debug(f"[ExpandContext] 展开消息 {mid} 失败（已忽略）: {e}")
        return ""


async def _describe_forward(bot: Any, forward_id: str, limit: int) -> str:
    """合并转发 → 逐条摘要（截断条数与单条长度）。"""
    try:
        message_id: Any = int(forward_id) if str(forward_id).isdigit() else forward_id
        resp = await bot.get_forward_msg(id=message_id)
    except Exception as e:
        logger.debug(f"[ExpandContext] 展开合并转发 {forward_id} 失败（已忽略）: {e}")
        return ""
    data = (resp or {}).get("data") if isinstance(resp, dict) else None
    if not isinstance(data, dict):
        return ""
    nodes = data.get("messages") or data.get("message") or []
    if not isinstance(nodes, list):
        return ""
    parts: list[str] = []
    for node in nodes[:MAX_FORWARD_NODES]:
        if not isinstance(node, dict):
            continue
        sender = node.get("sender") or {}
        label = str(sender.get("nickname") or sender.get("user_id") or "未知")
        text = _truncate(extract_msg_text(node.get("message") or node.get("content")), limit // 2 or 50)
        if text:
            parts.append(f"{label}: {text}")
    if len(nodes) > MAX_FORWARD_NODES:
        parts.append(f"…（共 {len(nodes)} 条，已省略）")
    return " ｜ ".join(parts)


async def _handle_expand(ctx, args: dict) -> str:
    """展开当前消息环境里未展开的内容。"""
    if ctx is None:
        return "error: 当前无会话上下文"
    bot = getattr(ctx, "bot", None)
    if bot is None:
        return "error: 当前上下文无可用 Bot"

    args = args or {}
    users = _int_ids(args.get("users"))[:MAX_ITEMS]
    messages = _str_ids(args.get("messages"))[:MAX_ITEMS]
    event = getattr(ctx, "event", None)
    self_ids = {
        str(getattr(ctx, "bot_id", "") or ""),
        str(getattr(event, "self_id", "") or "") if event is not None else "",
        str(getattr(event, "bot_id", "") or "") if event is not None else "",
    }
    self_ids.discard("")

    relation_hint = "指定展开"
    if not users and not messages:
        if event is None:
            return (
                "error: 没有指定要展开的对象，且当前没有触发消息可推导"
                "（可传 users / messages 指定）"
            )
        users, messages = derive_targets(event, self_ids)
        relation_hint = "本轮消息"
        if not users and not messages:
            return "本轮消息里没有需要展开的 @ 对象或被引用消息。"

    group_id = getattr(ctx, "group_id", None)
    runtime = getattr(ctx, "runtime", None)
    bot_id = str(getattr(runtime, "bot_id", "") or getattr(ctx, "bot_id", "") or "")
    limit = _limit(args)

    from_event_users, from_event_messages = (
        derive_targets(event, self_ids) if event is not None else ([], [])
    )

    async def _user_block(qq: str) -> str:
        relation = "当前消息 @ 的对象" if qq in from_event_users else f"{relation_hint}的用户"
        return await _describe_user(bot, group_id, qq, relation, bot_id=bot_id)

    async def _message_block(mid: str) -> str:
        relation = "当前消息引用的消息" if mid in from_event_messages else f"{relation_hint}的消息"
        return await _describe_message(bot, mid, limit, relation)

    blocks = await asyncio.gather(
        *[_user_block(qq) for qq in users],
        *[_message_block(mid) for mid in messages],
    )
    found = [b for b in blocks if b]
    if not found:
        return (
            "error: 没有取到任何可展开的内容"
            "（消息可能已过期、id 不合法、机器人无权限或连接不可用）。"
        )
    head = f"已展开 {len(found)} 项（{relation_hint}）："
    return head + "\n" + "\n".join(found)


def build_context_tools(runtime: Any, ctx: Any) -> list[ToolSpec]:
    """构造绑定当前 ToolContext 的上下文展开工具。"""

    async def _expand(_ctx, _args: dict) -> str:
        return await _handle_expand(ctx, _args)

    return [
        ToolSpec(
            name="expand_context",
            description=(
                "展开聊天环境里未展开的内容：某个 QQ 是谁（群名片/角色/头衔）、"
                "被引用的消息正文、合并转发的内容。\n"
                "当上下文或聊天记录里出现【未展开:…】标记，或用户问及某人/某条消息而你手上"
                "只有 id（如 @123、(123)、消息 id）时调用；不要凭 id 猜内容。\n"
                "不传参＝展开本轮触发消息里的 @ 对象与被引用消息；也可用 users / messages "
                "一次指定多个。图片与语音内容无法通过本工具转成文字，需要时请直接说明拿不到。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "users": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "要展开的 QQ 号，可多个。",
                    },
                    "messages": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要展开的消息 id，可多个（引用消息/合并转发）。",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "单条内容最大字符数，默认 500。",
                    },
                },
            },
            handler=_expand,
            permission="member",
            scopes=("*",),
            source="system",
            category="会话",
        ),
    ]
