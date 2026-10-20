"""上下文展开里的「解析与摘要」：把消息/用户/转发渲染成给模型看的一行行。

## 本模块只负责

OneBot 段解析（`_segments` / `_seg_type` …）、id 提取（`_int_ids` / `derive_targets`）、
以及把消息、用户、合并转发渲染成有长度上限的摘要（`summarize_*` / `_describe_*` /
`_render_forward_nodes`）。**不取数据、不注册工具**。

## 为什么单独成文件

`context_tools.py` 原本 1,059 行，前 390 行全是「怎么把一段消息说清楚」，与「什么时候
去取」和「怎么暴露成工具」无关。摘要口径要反复调（长度上限、转发嵌套、@/图片占位），
单独一个文件才好改。
"""

from __future__ import annotations

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


import re
import time
from typing import Any

from app.agent.context import display_names, nicknames
from app.agent.context.group import (
    _NON_TEXT_SEGMENTS,
    extract_msg_text,
    has_real_content,
)
from app.core.logger import logger

# 内容截断策略：**默认不截断**（0 = 不限制）。
# 这些工具的目的就是"把骨架补成血肉"，截断等于把血肉又削掉一块（实测出现过
# 「能花那么多时」这种砍在词中间的摘要）。需要限长时由调用方显式传 ``limit``。


DEFAULT_ITEM_CHARS = 0


MAX_ITEMS = 10


MAX_RECENT = 5


MAX_FORWARD_NODES = 0


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
            raw = str(data.get("card") or data.get("nickname") or "")
            # 共享昵称缓存：本次展开过的昵称，后续渲染背景块直接命中
            nicknames.remember(bot_id, group_id, qq, raw)
            if not data:
                return "", False
            # 展示值过显示名映射：句子型昵称/群名片不能原样进上下文
            shown = display_names.display_name(
                raw, qq, bot_id=bot_id, group_id=group_id, runtime=_runtime_of(bot)
            )
            bits = [f"昵称：{shown or '未知'}"]
            card_raw = str(data.get("card") or "")
            if card_raw:
                bits.append("群名片：" + display_names.display_name(
                    card_raw, qq, bot_id=bot_id, group_id=group_id, runtime=_runtime_of(bot)
                ))
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
        nickname = display_names.display_name(
            str(data.get("nickname") or ""), qq, runtime=_runtime_of(bot)
        ) or "未知"
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
        label = display_names.display_for_sender(
            data.get("sender") or {},
            bot_id=getattr(bot, "bot_id", ""),
            runtime=_runtime_of(bot),
        )
        text = extract_msg_text(data.get("message"))
        bits = [label]
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


def _render_forward_nodes(nodes: Any, *, depth: int = 0, seen: set[str] | None = None,
                          runtime: Any = None) -> str:
    """把转发节点数组渲染成 ``MM-DD HH:MM 昵称(QQ): 内容 ｜ …``。

    字段取法与 OneBot 返回一致：``sender.{card,nickname,user_id}``、``time``、
    ``message``（部分实现放在 ``content``）。昵称一律经显示名映射（句子型→``用户<QQ>``）。
    """
    if not isinstance(nodes, list):
        return ""
    seen = seen if seen is not None else set()
    lines: list[str] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        label = display_names.display_for_sender(node.get("sender") or {}, runtime=runtime)
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
    rendered = _render_forward_nodes(window, runtime=_runtime_of(bot))
    if MAX_FORWARD_NODES and len(nodes) > MAX_FORWARD_NODES:
        rendered += f" ｜ …（共 {len(nodes)} 条，已省略）"
    if not rendered:
        return "", "转发里没有可读文本"
    return _truncate(rendered, limit), ""


def _runtime_of(source: Any) -> Any:
    """从 ToolContext 或 Bot 上取 AgentRuntime（显示名映射需要它做灰区判定预热）。

    取不到就返回 None —— 映射层会退化成"安全名"，不影响任何渲染。
    """
    runtime = getattr(source, "runtime", None)
    if runtime is not None:
        return runtime
    return getattr(source, "agent_runtime", None) or getattr(source, "_runtime", None)
