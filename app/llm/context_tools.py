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
import re
import time
from dataclasses import dataclass, field
from typing import Any

from app.core.logger import logger
from app.llm import focus, nicknames
from app.llm.group_context import (
    _NON_TEXT_SEGMENTS,
    collect_at_ids,
    extract_history_messages,
    extract_msg_text,
    has_real_content,
)
from app.llm.tool import ToolSpec

# 内容截断策略：**默认不截断**（0 = 不限制）。
# 这些工具的目的就是"把骨架补成血肉"，截断等于把血肉又削掉一块（实测出现过
# 「能花那么多时」这种砍在词中间的摘要）。需要限长时由调用方显式传 ``limit``。
DEFAULT_ITEM_CHARS = 0
MAX_ITEMS = 10
# expand_recent 一次最多取几条
MAX_RECENT = 5
# 合并转发最多摘几条（0 = 全部）
MAX_FORWARD_NODES = 0
# 嵌套转发递归渲染的最大深度（防病态深度；超出时保留 id 让模型按 id 再展开）
MAX_FORWARD_DEPTH = 5


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
    """解析可选的 ``limit``（单条最大字符数）。

    默认 **0 = 不截断**；调用方显式给出正数时才限制（用于极端情况下的上下文保护）。
    """
    raw = args.get("limit")
    if raw in (None, ""):
        return DEFAULT_ITEM_CHARS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_ITEM_CHARS
    return max(0, value)


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


def has_forward_segment(message: Any) -> bool:
    """消息段里是否含合并转发（决定要不要去取转发内容）。"""
    return any(_seg_type(seg) == "forward" for seg in _segments(message))


def forward_inner_id(message: Any) -> str:
    """转发节点**内部** id（如 7686537322889496857）。

    仅作兜底：``get_forward_msg`` 正常要的是**承载转发的那条消息 id**（见
    ``_describe_forward`` 的说明），但如果某些实现只认内部 id，这里能救一次。
    """
    for seg in _segments(message):
        if _seg_type(seg) == "forward":
            return str(_seg_field(seg, "id", "") or "")
    return ""


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
    """按 ``limit`` 截断；``limit <= 0`` 表示不截断（默认行为）。"""
    text = (text or "").strip()
    if limit is None or limit <= 0:
        return text
    return text if len(text) <= limit else text[:limit] + "…"


def summarize_user(text: str) -> str:
    """从用户展开文本里抽一句话摘要（供焦点表/已展开标记使用）。

    适配第一人称格式：``（我看了下这个人）昵称：三哥；群名片：… [QQ 123] [关系：…]``
    ——方括号里的 id/关系不算摘要内容，遇到 ``[`` 即停。

    **不截断**：摘要是"已展开"时复用给模型的正文（``（这条我之前已经看过了）…``），
    截断会让模型看到的比实际更少（实测出现过「能花那么多时」这种砍在词中间的）。
    """
    body = str(text or "").lstrip("（").strip()
    for key in ("昵称：", "群名片："):
        m = re.search(key + r"([^；\[]+)", str(text or ""))
        if m and m.group(1).strip():
            return m.group(1).strip()
    return body


def summarize_message(text: str) -> str:
    """从消息展开文本里抽一句话摘要（谁 + 说了什么 / 转发了什么）。

    适配第一人称格式：
    ``（我翻到了这条消息）三哥(123)：正文 [id 999] [关系：…]``
    ``（我翻到了这条消息）三哥(123)：转发内容 —— 小明: 早 [id 999] [关系：…]``

    **不截断**（理由同 :func:`summarize_user`）。
    """
    body = str(text or "").strip()
    if not body:
        return ""
    m = re.search(r"转发内容 —— (.+?)(?:\s*\[id|\s*\[关系|$)", body)
    if m:
        return ("转发：" + m.group(1)).strip()
    # 去掉开头的"（我翻到了这条消息）"之类说明，再取"发送者：正文"
    stripped = re.sub(r"^（[^）]*）", "", body).strip()
    m = re.search(r"^(.+?)[：:](.+?)(?:\s*\[id|\s*\[关系|$)", stripped)
    if m:
        return f"{m.group(1)}：{m.group(2)}".strip()
    m = re.search(r"转发内容 —— (.+)", body)
    if m:
        return ("转发：" + m.group(1)).strip()
    return stripped


async def _describe_user(bot: Any, group_id: Any, qq: str, relation: str, *, bot_id: Any = "") -> tuple[str, bool]:
    """把一个 QQ 展开成"是谁"（群聊优先取群名片/角色）；返回 (文本, 是否取到内容)。

    文本用**第一人称"我看到的"**口吻（``（我看了下这个人）……``），而不是字段清单式的
    "【用户 123】昵称：…；relation：…"——后者读起来像一份待总结的报告，会把模型带向
    "复述/汇报"的语气。
    """
    try:
        if group_id not in (None, ""):
            resp = await bot.get_group_member_info(group_id=int(group_id), user_id=int(qq))
            data = (resp or {}).get("data") if isinstance(resp, dict) else None
            data = data if isinstance(data, dict) else {}
            nickname = str(data.get("card") or data.get("nickname") or "")
            # 共享昵称缓存：本次展开过的昵称，后续渲染背景块直接命中
            nicknames.remember(bot_id, group_id, qq, nickname)
            if not data:
                return "", False
            bits = [f"昵称：{nickname or '未知'}"]
            if data.get("card"):
                bits.append(f"群名片：{data.get('card')}")
            if data.get("role"):
                bits.append(f"角色：{data.get('role')}")
            if data.get("level"):
                bits.append(f"等级：{data.get('level')}")
            if data.get("title"):
                bits.append(f"头衔：{data.get('title')}")
            return f"（我看了下这个人）{'；'.join(bits)} [QQ {qq}] [关系：{relation}]", True
        resp = await bot.get_stranger_info(user_id=int(qq))
        data = (resp or {}).get("data") if isinstance(resp, dict) else None
        data = data if isinstance(data, dict) else {}
        if not data:
            return "", False
        nickname = str(data.get("nickname") or "") or "未知"
        return f"（我看了下这个人）昵称：{nickname} [QQ {qq}] [关系：{relation}]", True
    except Exception as e:
        logger.debug(f"[ExpandContext] 展开用户 {qq} 失败（已忽略）: {e}")
        return "", False


async def _describe_message(bot: Any, mid: str, limit: int, relation: str) -> tuple[str, bool]:
    """把一条消息 id 展开成"谁说的、说了什么"（含合并转发的一层内容）。

    返回 ``(文本, 是否取到正文)``：只有发送者、正文仍是占位时第二项为 False——
    调用方据此如实汇报，绝不能让模型以为"已展开"就直接开始回答。
    """
    try:
        data = await _fetch_message(bot, mid)
        if not isinstance(data, dict) or not data:
            return "", False
        sender = data.get("sender") or {}
        label = str(sender.get("card") or sender.get("nickname") or sender.get("user_id") or "未知")
        sender_id = str(sender.get("user_id") or "")
        text = extract_msg_text(data.get("message"))
        bits = [f"{label}" + (f"({sender_id})" if sender_id else "")]
        if has_real_content(text):
            bits.append(_truncate(text, limit))
        # 合并转发：再展开一层（一层深度的"引用链"，不递归，避免无界展开）。
        # 注意：get_forward_msg 要的是**承载转发的那条消息的 id**（也就是这里的 mid），
        # 不是转发节点内部的 forward id——后者是超长整型字符串（如 7686537322889496857），
        # 既超出 int32、也超出 JS 安全整数范围。内部 id 只作为兜底再试一次。
        forward_ok = False
        if has_forward_segment(data.get("message")):
            nodes, forward_error = await _describe_forward(bot, str(mid), limit)
            inner_id = forward_inner_id(data.get("message"))
            if not nodes and inner_id and inner_id != str(mid):
                nodes, fallback_error = await _describe_forward(bot, inner_id, limit)
                forward_error = forward_error or fallback_error
            if nodes:
                bits.append("转发内容 —— " + nodes)
                forward_ok = True
            else:
                # 如实报告失败原因：否则模型会以为拿到了内容，或者换个工具把同一件事再试一遍
                bits.append(f"转发内容没取到（{forward_error or '原因未知'}）")
        got_content = has_real_content(text) or forward_ok
        if not forward_ok and not has_real_content(text):
            bits.append("（正文没拿到）")
        head = "（我翻到了这条消息）" if got_content else "（这条只翻到一半）"
        return f"{head}{'：'.join(bits)} [id {mid}] [关系：{relation}]", got_content
    except Exception as e:
        logger.debug(f"[ExpandContext] 展开消息 {mid} 失败（已忽略）: {e}")
        return "", False


async def _fetch_message(bot: Any, mid: str) -> dict:
    """取单条消息（兼容 message_id 为字符串/超出 int32 的数字）。

    ``get_msg`` 的契约是 ``message_id: int``，但模型可能传进来一个转发 id；
    数字形式取不到时退回原始字符串再试一次，避免"消息存在却报取不到"。
    """
    raw = str(mid).strip()
    attempts: list[Any] = []
    if raw.lstrip("-").isdigit():
        attempts.append(int(raw))
    if raw not in attempts:
        attempts.append(raw)
    last: dict = {}
    for message_id in attempts:
        resp = await bot.get_msg(message_id=message_id)
        data = (resp or {}).get("data") if isinstance(resp, dict) else None
        if isinstance(data, dict) and data:
            return data
        last = resp if isinstance(resp, dict) else {}
    logger.debug(
        f"[ExpandContext] get_msg 未取到消息 {mid}: {last.get('retcode')} {last.get('message')}"
    )
    return {}


def _forward_time_prefix(ts: Any) -> str:
    """节点时间前缀 ``MM-DD HH:MM ``（与 group_context 的既有打标一致）。"""
    if ts in (None, ""):
        return ""
    try:
        return time.strftime("%m-%d %H:%M ", time.localtime(int(ts)))
    except (TypeError, ValueError, OSError):
        return ""


def _render_node_segments(segments: Any, *, depth: int, seen: set[str]) -> str:
    """渲染转发节点里的消息段，**对齐 OneBot 返回结构**。

    OneBot 的 ``get_forward_msg`` 节点里：
    - 嵌套转发是**带内联内容的**（``data.content`` 直接给出子节点数组）→ 递归渲染，
      而不是丢掉内容只留一个 ``[合并转发]``；
    - 回复段带 ``id``（``{"type":"reply","data":{"id":...}}``）→ 保留 id 写成
      ``[引用{id}]``，而不是无信息量的 ``[引用]``；
    - 其余非文本段沿用项目统一的占位名（``[图片]``/``[表情]``/``[语音]``…）。
    """
    if not isinstance(segments, list):
        return str(segments or "").strip()
    parts: list[str] = []
    for seg in segments:
        stype = _seg_type(seg)
        if stype == "text":
            parts.append(str(_seg_field(seg, "text", "") or ""))
        elif stype == "at":
            qq = str(_seg_field(seg, "qq", "") or "")
            name = str(_seg_field(seg, "nickname", "") or "")
            if qq in ("", "all", "0"):
                parts.append("@所有人")
            else:
                # OneBot 有时会附带 nickname（没有也不要紧，给 qq）
                parts.append(f"@{name}({qq})" if name else f"@{qq}")
        elif stype == "reply":
            reply_id = str(_seg_field(seg, "id", "") or "")
            parts.append(f"[引用{reply_id}]" if reply_id else "[引用]")
        elif stype == "forward":
            inner_id = str(_seg_field(seg, "id", "") or "")
            inner = _seg_field(seg, "content", None)
            if isinstance(inner, list) and inner and depth < MAX_FORWARD_DEPTH and inner_id not in seen:
                seen.add(inner_id)
                parts.append(
                    "[嵌套转发：" + _render_forward_nodes(inner, depth=depth + 1, seen=seen) + "]"
                )
            else:
                parts.append(f"[合并转发{(' ' + inner_id) if inner_id else ''}]")
        elif stype in _NON_TEXT_SEGMENTS:
            parts.append(f"[{_NON_TEXT_SEGMENTS[stype]}]")
        elif stype:
            parts.append(f"[{stype}]")
    return "".join(parts).strip()


def _render_forward_nodes(nodes: Any, *, depth: int = 0, seen: set[str] | None = None) -> str:
    """把转发节点数组渲染成 ``MM-DD HH:MM 昵称(QQ): 内容 ｜ …``。

    字段取法与 OneBot 返回一致：``sender.{card,nickname,user_id}``、``time``、
    ``message``（部分实现放在 ``content``）。
    """
    if not isinstance(nodes, list):
        return ""
    seen = seen if seen is not None else set()
    lines: list[str] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        sender = node.get("sender") or {}
        nick = str(sender.get("card") or sender.get("nickname") or sender.get("user_id") or "未知")
        uid = str(sender.get("user_id") or "")
        label = f"{nick}({uid})" if uid and uid != nick else nick
        body = _render_node_segments(
            node.get("message") if node.get("message") is not None else node.get("content"),
            depth=depth,
            seen=seen,
        )
        if not body:
            continue
        lines.append(f"{_forward_time_prefix(node.get('time'))}{label}: {body}")
    return " ｜ ".join(lines)


async def _describe_forward(bot: Any, forward_ref: str, limit: int) -> tuple[str, str]:
    """合并转发 → 逐条正文；返回 ``(内容, 失败原因)``。

    ``id`` 传**承载转发的那条消息的 id**（协议上 OneBot 也接受该消息的 id），
    并且一律按**字符串**传：这类 id 常是超出 int32 / JS 安全整数范围的长整型字符串，
    强转 int 会丢精度并被拒为「1200 消息已过期或者为内层消息」。
    """
    try:
        resp = await bot.get_forward_msg(id=str(forward_ref))
    except Exception as e:
        logger.debug(f"[ExpandContext] 展开合并转发 {forward_ref} 失败（已忽略）: {e}")
        return "", str(e)
    if isinstance(resp, dict) and resp.get("status") not in (None, "ok"):
        return "", f"{resp.get('retcode')} {resp.get('message') or ''}".strip()
    data = (resp or {}).get("data") if isinstance(resp, dict) else None
    if not isinstance(data, dict):
        return "", "响应为空"
    nodes = data.get("messages") or data.get("message") or []
    if not isinstance(nodes, list):
        return "", "响应结构异常"
    # ``MAX_FORWARD_NODES=0`` 表示不限制（默认）：转发本来就常是"要看的正文"，
    # 砍条数等于把用户想让它看的内容丢掉。
    window = nodes if not MAX_FORWARD_NODES else nodes[:MAX_FORWARD_NODES]
    rendered = _render_forward_nodes(window)
    if MAX_FORWARD_NODES and len(nodes) > MAX_FORWARD_NODES:
        rendered += f" ｜ …（共 {len(nodes)} 条，已省略）"
    if not rendered:
        return "", "转发里没有可读文本"
    return _truncate(rendered, limit), ""


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
        from app.llm.nicknames import resolve_nicknames

        at_names = await resolve_nicknames(
            bot, group_id,
            collect_at_ids([m for m in messages if isinstance(m, dict)]),
            bot_id=bot_id,
        )

    for msg in messages[-count:]:
        if not isinstance(msg, dict):
            continue
        sender = msg.get("sender") or {}
        label = str(sender.get("card") or sender.get("nickname") or sender.get("user_id") or "未知")
        sender_id = str(sender.get("user_id") or "")
        msg_id = str(msg.get("message_id") or msg.get("real_id") or msg.get("message_seq") or "")
        text = extract_msg_text(msg.get("message"), at_names, False, msg_id)
        bits = [f"{label}" + (f"({sender_id})" if sender_id else "")]
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

    from app.llm.image import resolve_images

    lines: list[str] = []
    queued = 0
    for mid in messages:
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
        who = sender.get("card") or sender.get("nickname") or sender.get("user_id") or "未知"
        images = await resolve_images(
            _ImageEvent(image_segs), bot=bot,
            max_images=len(image_segs), max_bytes=_max_image_bytes(ctx),
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
        lines.append("（图片已记为本轮补全，**下一轮对话里你会直接看到它**；先别猜图片内容）")
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


def _max_image_bytes(ctx) -> int:
    from app.llm.image import DEFAULT_MAX_IMAGE_BYTES, max_image_bytes

    config = getattr(getattr(ctx, "runtime", None), "config", None)
    if config is None or not hasattr(config, "get"):
        return DEFAULT_MAX_IMAGE_BYTES
    try:
        return max_image_bytes(config)
    except Exception:  # noqa: BLE001
        return DEFAULT_MAX_IMAGE_BYTES


def _extra(ctx) -> dict:
    """取 ToolContext.extra；缺失时补一个（注意不能写 ``or {}``，空 dict 会被丢掉）。"""
    extra = getattr(ctx, "extra", None)
    if not isinstance(extra, dict):
        extra = {}
        try:
            setattr(ctx, "extra", extra)
        except Exception as e:  # noqa: BLE001 —— 只读 ctx 时不至于崩
            logger.debug(f"[Expand] 无法写入 ToolContext.extra: {e}")
    return extra


def _record_round_images(ctx, images: list[dict]) -> None:
    """把"本轮要随请求传回的图片"记到 ToolContext.extra（由 chat 落地）。"""
    try:
        bucket = _extra(ctx).setdefault("round_images", [])
        for image in images:
            if isinstance(image, dict) and image.get("value") and image not in bucket:
                bucket.append(dict(image))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[ExpandImage] 记录待传图片失败（已忽略）: {e}")


def drain_round_images(ctx) -> list[dict]:
    """取出并清空"本轮待随请求传回"的图片（tool_ctx 可能为 None）。"""
    extra = getattr(ctx, "extra", None)
    if not isinstance(extra, dict):
        return []
    images = extra.pop("round_images", []) or []
    return [i for i in images if isinstance(i, dict) and i.get("value")]


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
                "取回历史消息里的图片（图片会在**下一轮**随请求提供，届时你直接看得到）。\n"
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
