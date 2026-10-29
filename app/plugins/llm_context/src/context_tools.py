"""按需展开上下文的系统级工具（``expand_recent`` / ``expand_message`` / ``expand_user``）。

定位：把渲染层标出来的「骨架」补成「血肉」。渲染层已经尽力预展开（@ 昵称、触发消息的
引用），但仍有三类内容只能按需取：更早/被引用的消息正文、合并转发内容、某个 QQ 是谁。

按**意图分区**拆成三个工具（而不是一个通用大工具）——通用工具只能写"出现未展开标记时调用"
这种抽象触发条件，模型无从判断该用哪个；分区后每个工具的 description 都能写清"何时必须调用"：

- ``expand_recent``：按**位置**取（"上一条/刚才那条/消息里的"）——不需要 id；
- ``expand_message``：按 **id** 取消息正文（引用 / 合并转发）；
- ``expand_user``：按 **QQ** 取身份（昵称/群名片/角色）。

设计要点（照抄 ``session_tools`` 的既有约定）：
- **支持批量**：可一次传多个 id/QQ，内部并发执行——串行会把墙钟时间叠加到超时；
- **自解释返回**：每条结果都带 ``relation``（"当前消息 @ 的对象" / "当前消息引用的消息"）；
- **登记优先**：本会话取回过的 id 直接复用摘要（零 API 调用），不再"重读取"；
- **如实汇报**：只取到发送者、正文仍是占位时归入"部分信息"，绝不让模型以为拿到了内容；
- **只承诺做得到的事**：图片/语音无法转文字不在此工具能力范围内。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from app.agent.context import display_names
from app.agent.context.group import (
    collect_at_ids,
    extract_history_messages,
    extract_msg_text,
    has_real_content,
)
from app.agent.tools.round_images import (  # noqa: F401 - 重导出保持原路径可用
    _extra,
    _max_image_bytes,
    _record_round_images,
    _runtime_config,
    _session_entries,
    append_round_images,
    drain_round_images,
)
from app.agent.tools.spec import ToolSpec
from app.core.logger import logger

from . import focus
from .context_summary import (  # noqa: F401 - 重导出保持原路径可用
    DEFAULT_ITEM_CHARS,
    MAX_FORWARD_DEPTH,
    MAX_FORWARD_NODES,
    MAX_ITEMS,
    MAX_RECENT,
    _as_list,
    _describe_forward,
    _describe_message,
    _describe_user,
    _fetch_message,
    _forward_time_prefix,
    _int_ids,
    _limit,
    _render_forward_nodes,
    _render_node_segments,
    _runtime_of,
    _seg_field,
    _seg_type,
    _segments,
    _str_ids,
    _truncate,
    derive_targets,
    forward_inner_id,
    has_forward_segment,
    summarize_message,
    summarize_user,
)

# 内容截断策略：**默认不截断**（0 = 不限制）。
# 这些工具的目的就是"把骨架补成血肉"，截断等于把血肉又削掉一块（实测出现过
# 「能花那么多时」这种砍在词中间的摘要）。需要限长时由调用方显式传 ``limit``。
# expand_recent 一次最多取几条
# 合并转发最多摘几条（0 = 全部）
# 嵌套转发递归渲染的最大深度（防病态深度；超出时保留 id 让模型按 id 再展开）


@dataclass
class EntityResult:
    """一次取回的汇总结果（工具输出与确定性预取共用）。"""

    blocks: list[str] = field(default_factory=list)    # 已取到内容
    partial: list[str] = field(default_factory=list)   # 只取到部分信息（正文未取到）
    refs: list[str] = field(default_factory=list)      # 本次涉及的 id（去重用）

    @property
    def ok(self) -> bool:
        return bool(self.blocks or self.partial)

    @property
    def has_content(self) -> bool:
        return bool(self.blocks)

    def render(self) -> str:
        """工具结果文本。

        用**第一人称"我看到的"**并**去掉"已展开 N 项"这类汇总头**——汇总头读起来像一份
        待总结的报告，直接把模型带向"复述/汇报"的语气。（逐条块自身已标明
        "（我翻到了这条消息）/（这条只翻到一半）"，无需再套一层统计。）
        """
        if not self.ok:
            # 全失败：保持 "error:" 前缀（项目约定：工具失败结果可被识别与统计）
            return (
                "error: 没有取到任何可展开的内容"
                "（消息可能已过期、id 不合法、机器人无权限或连接不可用）。"
            )
        return "\n".join([*self.blocks, *self.partial])


def _entity_ids(ctx) -> tuple[Any, str, str, Any, set[str]]:
    """从 ToolContext 取出 (bot, bot_id, session_id, group_id, self_ids)。"""
    event = getattr(ctx, "event", None)
    runtime = getattr(ctx, "runtime", None)
    self_ids = {
        str(getattr(runtime, "bot_id", "") or ""),
        str(getattr(ctx, "bot_id", "") or ""),
        str(getattr(event, "self_id", "") or "") if event is not None else "",
        str(getattr(event, "bot_id", "") or "") if event is not None else "",
    }
    self_ids.discard("")
    return (
        getattr(ctx, "bot", None),
        str(getattr(runtime, "bot_id", "") or getattr(ctx, "bot_id", "") or ""),
        str(getattr(ctx, "session_id", "") or ""),
        getattr(ctx, "group_id", None),
        self_ids,
    )


def _record_expansion(
    ctx, kind: str, ref: Any, *, summary: str = "", content: str = "", source: str = ""
) -> None:
    """把"本次取回"记进本轮记账本（请求收尾时落进历史补全登记）。

    失败静默：记账只是增强，不该影响工具本身的返回。
    """
    try:
        ledger = (getattr(ctx, "extra", None) or {}).get("expansion_ledger")
        if ledger is not None:
            ledger.record(kind, ref, summary, content=content, source=source)
    except Exception as e:  # noqa: BLE001 —— 记账失败不影响工具返回，但要留痕
        logger.debug(f"[Expand] 补全记账失败（已忽略）: {e}")


async def fetch_entities(
    ctx,
    *,
    users: list | tuple = (),
    messages: list | tuple = (),
    limit: int = DEFAULT_ITEM_CHARS,
    source: str = "expand",
) -> EntityResult:
    """按 id 取回"人 / 消息"内容（公共 API：工具与确定性预取共用）。

    - 先查焦点表登记：本会话取回过的 id 直接复用摘要，**零 API 调用**（杜绝重读取）；
    - 成功取回时写回登记（渲染层据此把标记升级为"已展开"）；
    - 并发执行、逐项如实汇报（只取到发送者、正文仍是占位 → 进 ``partial``）。
    """
    result = EntityResult()
    if ctx is None:
        return result
    bot, bot_id, session_id, group_id, self_ids = _entity_ids(ctx)
    if bot is None:
        return result
    event = getattr(ctx, "event", None)
    from_event_users, from_event_messages = (
        derive_targets(event, self_ids) if event is not None else ([], [])
    )

    async def _user_block(qq: str) -> tuple[str, bool]:
        relation = "当前消息 @ 的对象" if qq in from_event_users else "指定展开的用户"
        cached = focus.summary_of(bot_id, session_id, qq) if session_id else ""
        if cached:
            return f"（这个人我之前已经看过）昵称：{cached} [QQ {qq}] [关系：{relation}]", True
        text, ok = await _describe_user(bot, group_id, qq, relation, bot_id=bot_id)
        if ok and text:
            summary = summarize_user(text)
            focus.note(bot_id, session_id, qq, kind="user", label=f"用户{qq}",
                       summary=summary, source=source)
            _record_expansion(ctx, "user", qq, summary=summary, content=text, source=source)
        return text, ok

    async def _message_block(mid: str) -> tuple[str, bool]:
        relation = "当前消息引用的消息" if mid in from_event_messages else "指定展开的消息"
        cached = focus.summary_of(bot_id, session_id, mid) if session_id else ""
        if cached:
            return f"（这条我之前已经看过了）{cached} [id {mid}] [关系：{relation}]", True
        text, ok = await _describe_message(bot, mid, limit, relation)
        if ok and text:
            summary = summarize_message(text)
            focus.note(bot_id, session_id, mid, kind="message", label=f"消息{mid}",
                       summary=summary, source=source)
            _record_expansion(ctx, "message", mid, summary=summary, content=text, source=source)
        return text, ok

    pairs = await asyncio.gather(
        *[_user_block(qq) for qq in users],
        *[_message_block(mid) for mid in messages],
    )
    result.refs = [str(x) for x in list(users) + list(messages)]
    for text, got in pairs:
        if not text:
            continue
        (result.blocks if got else result.partial).append(text)
    return result


async def fetch_recent(ctx, count: int = 1, *, limit: int = DEFAULT_ITEM_CHARS) -> EntityResult:
    """按**相对位置**取回最近 N 条消息（含其中的合并转发内容）。

    这是"上一条 / 刚才那条 / 消息里的那个"这类**按位置指代**的落地手段：不需要任何 id。
    取回结果同样写入焦点登记。
    """
    result = EntityResult()
    if ctx is None:
        return result
    bot, bot_id, session_id, group_id, self_ids = _entity_ids(ctx)
    if bot is None:
        return result
    is_private = not str(getattr(ctx, "session_id", "") or "").startswith("group_")
    count = max(1, min(int(count or 1), MAX_RECENT))

    try:
        if is_private:
            resp = await bot.get_msg_history(
                group_id=0, user_id=int(getattr(ctx, "user_id", 0) or 0),
                count=count, reverse_order=False,
            )
        else:
            resp = await bot.get_msg_history(
                group_id=int(group_id or 0), user_id=0, count=count, reverse_order=False,
            )
    except Exception as e:
        logger.debug(f"[ExpandRecent] 取最近消息失败（已忽略）: {e}")
        return result

    messages = extract_history_messages(resp)
    if not messages:
        return result
    at_names: dict[str, str] = {}
    if group_id not in (None, ""):
        from app.agent.context.nicknames import resolve_nicknames

        at_names = await resolve_nicknames(
            bot, group_id,
            collect_at_ids([m for m in messages if isinstance(m, dict)]),
            bot_id=bot_id,
        )

    for msg in messages[-count:]:
        if not isinstance(msg, dict):
            continue
        label = display_names.display_for_sender(
            msg.get("sender") or {},
            bot_id=bot_id,
            group_id=group_id,
            runtime=_runtime_of(ctx),
        )
        msg_id = str(msg.get("message_id") or msg.get("real_id") or msg.get("message_seq") or "")
        text = extract_msg_text(msg.get("message"), at_names, False, msg_id)
        bits = [label]
        ok = has_real_content(text)
        if ok:
            bits.append(_truncate(text, limit))
        if has_forward_segment(msg.get("message")):
            nodes, forward_error = await _describe_forward(bot, msg_id, limit)
            inner_id = forward_inner_id(msg.get("message"))
            if not nodes and inner_id and inner_id != msg_id:
                nodes, fallback_error = await _describe_forward(bot, inner_id, limit)
                forward_error = forward_error or fallback_error
            if nodes:
                bits.append("转发内容 —— " + nodes)
                ok = True
            else:
                bits.append(f"转发内容没取到（{forward_error or '原因未知'}）")
        if not ok:
            bits.append("（正文没拿到）")
        head = "（我刚翻的最近消息" if ok else "（最近这条只翻到一半"
        suffix = f"，id {msg_id}）" if msg_id else "）"
        block = f"{head}{suffix}{'：'.join(bits)}"
        (result.blocks if ok else result.partial).append(block)
        if msg_id:
            result.refs.append(msg_id)
        if ok and msg_id:
            summary = summarize_message(block)
            focus.note(bot_id, session_id, msg_id, kind="message",
                       label=f"消息{msg_id}", summary=summary, source="expand")
            _record_expansion(
                ctx, "message", msg_id, summary=summary, content=block, source="expand_recent"
            )
    return result


def _context_guard(ctx) -> str:
    """公共前置检查：无上下文/无 Bot 时返回可行动的错误文本，否则返回空串。"""
    if ctx is None:
        return "error: 当前无会话上下文"
    if getattr(ctx, "bot", None) is None:
        return "error: 当前上下文无可用 Bot"
    return ""


async def _handle_expand(ctx, args: dict) -> str:
    """（内部路由）展开当前消息环境里未展开的内容。"""
    guard = _context_guard(ctx)
    if guard:
        return guard
    args = args or {}
    users = _int_ids(args.get("users"))[:MAX_ITEMS]
    messages = _str_ids(args.get("messages"))[:MAX_ITEMS]
    if not users and not messages:
        event = getattr(ctx, "event", None)
        if event is None:
            return (
                "error: 没有指定要展开的对象，且当前没有触发消息可推导"
                "（可传 users / messages 指定）"
            )
        _bot, _bid, _sid, _gid, self_ids = _entity_ids(ctx)
        users, messages = derive_targets(event, self_ids)
        if not users and not messages:
            return "本轮消息里没有需要展开的 @ 对象或被引用消息。"
    return (
        await fetch_entities(ctx, users=users, messages=messages, limit=_limit(args))
    ).render()


async def _handle_expand_recent(ctx, args: dict) -> str:
    """展开最近 N 条消息（含合并转发内容）。"""
    guard = _context_guard(ctx)
    if guard:
        return guard
    args = args or {}
    try:
        count = int(args.get("count") or 1)
    except (TypeError, ValueError):
        count = 1
    result = await fetch_recent(ctx, count=count, limit=_limit(args))
    if not result.ok:
        return "error: 没有取到最近的消息（可能是新会话、连接不可用或没有历史）。"
    return result.render()


async def _handle_expand_message(ctx, args: dict) -> str:
    """按 id 展开消息（引用 / 合并转发）。"""
    guard = _context_guard(ctx)
    if guard:
        return guard
    args = args or {}
    messages = _str_ids(args.get("messages"))[:MAX_ITEMS]
    if not messages:
        return "error: 请给出要展开的消息 id（messages 数组）。"
    return (await fetch_entities(ctx, messages=messages, limit=_limit(args))).render()


async def _handle_expand_user(ctx, args: dict) -> str:
    """按 QQ 展开成员身份。"""
    guard = _context_guard(ctx)
    if guard:
        return guard
    args = args or {}
    users = _int_ids(args.get("users"))[:MAX_ITEMS]
    if not users:
        return "error: 请给出要展开的 QQ 号（users 数组）。"
    return (await fetch_entities(ctx, users=users, limit=_limit(args))).render()


async def _handle_expand_image(ctx, args: dict) -> str:
    """按消息 id 取回历史消息里的图片，供视觉模型"直接看到"。

    两条路，**本地优先**：

    1. 会话历史里那条消息已经落过盘（``data.media``）→ 直接读本地字节，连 OneBot 都不用问；
       QQ 侧消息过期了也照样能给模型看图；
    2. 没有本地指向 → 再 ``get_msg`` 取回原消息、解析并落盘（为下一轮留指向）。

    返回文本说明（已取回的消息 id / 图片数量 / 发送者），并把图片载荷排进
    "本轮稍后随请求传回"的队列（由 ``drain_round_images`` 取出）。
    非视觉模型拿到的是文字说明 + 占位，不会因此报错。
    """
    guard = _context_guard(ctx)
    if guard:
        return guard
    args = args or {}
    messages = _str_ids(args.get("messages"))[:MAX_ITEMS] or _str_ids(args.get("message_id"))[:MAX_ITEMS]
    if not messages:
        return "error: 请给出要取图片的消息 id（messages 数组）。"
    bot = getattr(ctx, "bot", None)
    if bot is None:
        return "error: 当前上下文无可用 Bot"

    from app.agent.media.image import resolve_images
    from app.agent.media.store import message_media_refs

    bot_id = getattr(ctx, "bot_id", "") or getattr(bot, "bot_id", "") or getattr(
        getattr(ctx, "runtime", None), "bot_id", ""
    )
    local_entries = _session_entries(ctx)
    lines: list[str] = []
    queued = 0
    for mid in messages:
        local_refs = message_media_refs(local_entries, mid)
        if local_refs:
            image_segs = [{"type": "image", "data": {"media": dict(ref)}} for ref in local_refs]
            who = "（本地缓存）"
            images = await resolve_images(
                _ImageEvent(image_segs), bot=bot, bot_id=bot_id,
                max_images=len(image_segs), max_bytes=_max_image_bytes(ctx),
                config=_runtime_config(ctx), segments=image_segs,
            )
            if images:
                queued += len(images)
                _record_round_images(ctx, images)
                _record_expansion(
                    ctx, "image", mid,
                    summary=f"会话历史里的 {len(images)} 张图（本地缓存）",
                    content="[本地缓存的图片]", source="expand_image",
                )
                lines.append(f"消息 {mid}：已从本地缓存取回 {len(images)} 张图片，图片随本条一起提供")
                continue
        try:
            resp = await bot.get_msg(int(mid)) if str(mid).isdigit() else {}
        except Exception as e:  # noqa: BLE001 —— 取不到就如实说明
            logger.debug(f"[ExpandImage] get_msg({mid}) 失败: {e}")
            resp = {}
        data = (resp or {}).get("data") or {}
        segments = data.get("message") or []
        image_segs = [
            seg for seg in segments
            if (seg.get("type") if isinstance(seg, dict) else getattr(seg, "type", "")) == "image"
        ]
        if not image_segs:
            lines.append(f"消息 {mid}：没有图片（可能不是图片消息或消息已过期）")
            continue
        sender = data.get("sender") or {}
        who = display_names.display_for_sender(
            sender,
            bot_id=bot_id,
            group_id=getattr(ctx, "group_id", ""),
            runtime=_runtime_of(ctx),
        )
        images = await resolve_images(
            _ImageEvent(image_segs), bot=bot, bot_id=bot_id,
            max_images=len(image_segs), max_bytes=_max_image_bytes(ctx),
            config=_runtime_config(ctx),
        )
        if not images:
            lines.append(f"消息 {mid}：有 {len(image_segs)} 张图，但拿不到可用图片地址")
            continue
        queued += len(images)
        _record_round_images(ctx, images)
        _record_expansion(
            ctx, "image", mid,
            summary=f"{who} 发的 {len(images)} 张图（已取回）",
            content=f"{who}: [图片 x{len(images)}]", source="expand_image",
        )
        lines.append(f"消息 {mid}：已取回 {who} 发的 {len(images)} 张图片，图片随本条一起提供")
    if queued:
        # 措辞要贴合实际时机：图片会在**本轮的下一次请求**里随消息一起送达（chat 的工具轮
        # 边界与补图轮都会抽走 round_images）。此前写的是「下一轮对话里你会直接看到它」，
        # 而当时根本没有代码把图片递出去 —— 模型只能凭文字瞎猜，用户看到的是编造的描述。
        lines.append("（图片已记为本轮补全材料，**会在本轮的下一次请求里随消息一起提供**；本轮先别猜图片内容）")
    return "\n".join(lines) if lines else "error: 没有取到任何图片"


class _ImageEvent:
    """最小事件替身：让 ``image.resolve_images`` 能处理一批图片段。"""

    def __init__(self, segments: list) -> None:
        self.message = [
            seg if not isinstance(seg, dict) else _SegView(seg) for seg in segments
        ]


class _SegView:
    def __init__(self, seg: dict) -> None:
        self.type = str(seg.get("type", ""))
        self.data = dict(seg.get("data", {}) or {})


def _context_spec(name: str, description: str, parameters: dict, handler) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        parameters=parameters,
        handler=handler,
        permission="member",
        scopes=("*",),
        source="system",
        category="会话",
        # 这些工具的任务就是"把骨架补成血肉"：结果**不截断**（0=不限制）。
        # 实测过截断的代价——摘要被砍成「能花那么多时」，模型据此答错。
        max_result=0,
    )


def build_context_tools(runtime: Any, ctx: Any) -> list[ToolSpec]:
    """构造绑定当前 ToolContext 的上下文展开工具。

    按**意图分区**（这也是"工具语义含糊"的修法）：按位置取 / 按 id 取消息 / 按 QQ 取人，
    每个工具的 description 都能写清"什么时候必须调用"，模型不必在一个通用工具里猜。
    """

    async def _recent(_ctx, _args: dict) -> str:
        return await _handle_expand_recent(ctx, _args)

    async def _message(_ctx, _args: dict) -> str:
        return await _handle_expand_message(ctx, _args)

    async def _user(_ctx, _args: dict) -> str:
        return await _handle_expand_user(ctx, _args)

    async def _image(_ctx, _args: dict) -> str:
        return await _handle_expand_image(ctx, _args)

    return [
        _context_spec(
            "expand_recent",
            (
                "取回**最近几条消息**的实际内容（含其中的合并转发内容）。\n"
                "当用户说“上一条消息/刚才那条/消息里的那个/你刚说的”这类**按位置指代**，"
                "或你需要知道群里刚刚发生了什么时调用；不需要任何 id。\n"
                "默认取最近 1 条；拿不准用户指哪一条时取最近 2~3 条一起看。"
            ),
            {
                "type": "object",
                "properties": {
                    "count": {"type": "integer", "description": "取最近几条，1~5，默认 1。"},
                    "limit": {"type": "integer", "description": "单条内容最大字符数；不传=完整返回。"},
                },
            },
            _recent,
        ),
        _context_spec(
            "expand_message",
            (
                "按 id 展开消息正文：被引用的消息、合并转发的内容。\n"
                "当上下文或聊天记录里出现【未展开:引用456】/【未展开:合并转发456】标记，"
                "或你手上有具体消息 id 时调用；可一次传多个 id。\n"
                "不要凭 id 猜内容；取不到时如实说明，不要编造。"
            ),
            {
                "type": "object",
                "properties": {
                    "messages": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要展开的消息 id，可多个（引用消息/合并转发）。",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "单条内容最大字符数；不传=完整返回（默认不截断）。",
                    },
                },
            },
            _message,
        ),
        _context_spec(
            "expand_user",
            (
                "按 QQ 号展开“这个人是谁”：昵称/群名片/角色/头衔。\n"
                "当用户问“某人是谁”“@123 是谁”，或记录里出现【未展开:用户123】时调用；"
                "可一次传多个 QQ。私聊里只能查当前对话对象。"
            ),
            {
                "type": "object",
                "properties": {
                    "users": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "要展开的 QQ 号，可多个。",
                    },
                },
            },
            _user,
        ),
        _context_spec(
            "expand_image",
            (
                "取回历史消息里的图片（图片会在**本轮的下一次请求**里随消息提供，届时你直接看得到）。\n"
                "当聊天记录里出现 [图片#123] / [语音#123] 这类带 id 的占位、"
                "用户说“看看那张图/上一条发的图”、或你判断某条历史消息里有图且与当前问题相关时调用。\n"
                "只取必要的图（一次 1~2 张），不要为了凑热闹把历史图片全翻出来。"
            ),
            {
                "type": "object",
                "properties": {
                    "messages": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要取图片的消息 id（形如 [图片#123] 里的 123），可多个。",
                    },
                },
            },
            _image,
        ),
    ]
