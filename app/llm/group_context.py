"""群聊环境上下文构建（不改动“非 @ 不入会话历史”的现有策略）。

用途：
- 在 `include_pre_history` 开启时，把群聊最近在线消息格式化为“带发送者、时间”的背景文本；
- 组装群名 / 群号 / 当前时间 / 最近记录等 system 提示块，供普通对话、主动消息、定时任务复用。

消息打标统一约定（换用消融测试验证过的最优打标方式）：
- 群聊：``MM-DD HH:MM 昵称(QQ): 内容`` —— 昵称相同用 QQ 区分，核心是“谁 + 什么时候”；
- 私聊：``MM-DD HH:MM 我/对方: 内容`` —— 私聊只有两方，不需要昵称，用角色（我/对方）即可分清。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core.logger import logger

# 非文本消息段的展示名，避免模型完全看不到非文本消息
_NON_TEXT_SEGMENTS = {
    "image": "图片",
    "face": "表情",
    "record": "语音",
    "video": "视频",
    "file": "文件",
    "share": "分享",
    "music": "音乐",
    "at": "AT",
    "reply": "引用",
    "forward": "合并转发",
}

# bot 自己的固定标签（群聊/私聊一致）；私聊对方用“对方”，不展示昵称
SELF_TAG = "我"
PRIVATE_OTHER_TAG = "对方"

# ==================== “未展开”标记 ====================
# 只标记**可被解决的缺口**（marker must be actionable）：
# - @ 用户：本轮尝试反查过昵称但没取到（无权限/退群/连接异常）→ 标记后可让模型按需展开；
# - 引用 / 合并转发：正文没有被内联，需要按 id 取。
# 图片/语音/文件等「内容型」缺口在无对应能力时**保持 ``[图片]`` 这类纯占位**，
# 标成“未展开”只会诱导模型空转或谎称无法回答。
UNRESOLVED_AT = "【未展开:用户{qq}】"
UNRESOLVED_REPLY = "【未展开:引用{id}】"
UNRESOLVED_FORWARD = "【未展开:合并转发】"
# 合并转发标记里的 id 是**承载转发的那条消息的 id**（不是转发节点内部的 forward id）：
# 展开入口 ``get_forward_msg(id=...)`` 要的就是这个 id；内部 id 是可能超出
# int32 / JS 安全整数范围的长整型字符串，传进去会被 NapCat 拒为
# 「1200 消息已过期或者为内层消息」。渲染时由调用方把整条消息 id 传进来
# （``format_online_history`` 取 ``message_id``，``enhance`` 取 ``reply_id``）。
UNRESOLVED_FORWARD_ID = "【未展开:合并转发{id}】"

# ==================== “已展开”标记（状态迁移） ====================
# 本会话已经取回过内容的 id 不再标"未展开"，而是给出摘要——这样：
# - 模型知道"这个我看过了"（不必重复取，也不会以为还缺）；
# - 下一轮请求仍能看到上一轮讨论过的内容（工具结果本身不进会话历史）。
EXPANDED_REPLY = "【已展开:引用{id} → {summary}】"
EXPANDED_FORWARD = "【已展开:合并转发{id} → {summary}】"

# 缺口扫描：与上面的标记格式保持同源（渲染出什么就扫什么）
_UNRESOLVED_RE = re.compile(r"【未展开:([^】]+)】")
# 占位符、未展开标记、已展开标记都不算"真实内容"
_PLACEHOLDER_RE = re.compile(r"【(?:未展开|已展开):[^】]*】|\[[^\]]{1,10}\]")


@dataclass
class ExpandedRefs:
    """已展开映射（由 ``focus.expanded_refs`` 之类的登记表提供）。

    分开两个字典避免"QQ 号与消息 id 数字恰好相同"时的语义串号。
    """

    messages: dict[str, str] = field(default_factory=dict)
    users: dict[str, str] = field(default_factory=dict)

    def message_summary(self, ref: Any) -> str:
        return self.messages.get(str(ref or "").strip(), "")

    def user_name(self, qq: Any) -> str:
        return self.users.get(str(qq or "").strip(), "")


def _expanded_marker(kind: str, ref: str, summary: str) -> str:
    text = _clip_summary(summary)
    if kind == "reply":
        return EXPANDED_REPLY.format(id=ref, summary=text)
    return EXPANDED_FORWARD.format(id=ref, summary=text)


def _clip_summary(summary: str, limit: int = 80) -> str:
    text = " ".join(str(summary or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def lookup_expanded(bot_id: Any, session_id: Any) -> ExpandedRefs:
    """按会话查"已展开"登记（懒加载 focus，避免模块级循环依赖）。"""
    if bot_id in (None, "") or session_id in (None, ""):
        return ExpandedRefs()
    try:
        from app.llm import focus as _focus

        data = _focus.expanded_refs(bot_id, session_id)
    except Exception as e:
        # 登记表读取失败不影响渲染主流程，按"都还没展开"处理并留痕
        logger.debug(f"[GroupContext] 读取已展开登记失败（按未展开处理）: {e}")
        return ExpandedRefs()
    return ExpandedRefs(
        messages=dict(data.get("messages") or {}),
        users=dict(data.get("users") or {}),
    )


def has_real_content(text: Any) -> bool:
    """文本里除占位符/未展开标记外是否还有真实内容。

    用途：判断"这条引用/消息是不是其实什么都没拿到"——只有 ``[合并转发]`` 这类占位时，
    不能对模型宣称"已展开"（否则它会以为拿到了内容，直接开始回答）。
    """
    return bool(_PLACEHOLDER_RE.sub("", str(text or "")).strip())


def unresolved_items(text: Any) -> list[str]:
    """列出文本里的未展开项（去重保序），如 ``['用户123', '引用456']``。"""
    seen: list[str] = []
    for item in _UNRESOLVED_RE.findall(str(text or "")):
        if item not in seen:
            seen.append(item)
    return seen


def unresolved_summary(text: Any) -> str:
    """生成「缺口摘要」行；没有缺口时返回空串。

    模型不必自己逐行扫描上下文就能知道"缺什么"，也不必靠提示词记住标记形态：
    ``【本段含 2 处未展开内容：用户123、引用456；可调用 expand_context 展开】``

    同一项出现多次时标明"（N 类）"——否则"9 处"后面只列 6 项会让模型以为漏看了。
    """
    raw = str(text or "")
    items = unresolved_items(raw)
    if not items:
        return ""
    total = len(_UNRESOLVED_RE.findall(raw))
    kinds = f"（{len(items)} 类）" if total != len(items) else ""
    return (
        f"【本段含 {total} 处未展开内容{kinds}：{'、'.join(items)}；"
        "可调用 expand_context 展开后再回答】"
    )

# 已自带“发送者/发送者昵称/发送了/消息正文/时间”自描述内容（LLM 增强模块 llm_enhance 产出的散文块）。
# 这类内容再套外层“MM-DD HH:MM 昵称(QQ):”会变成重复脏信息，渲染时应原样输出。
# 同时兼容旧历史（发送者/发送了）与当前单行格式（昵称(QQ): 正文）。
_ENHANCED_RE = re.compile(
    r"(?:^|\n)(?:发送者：|发送者昵称：|发送了：|消息正文：)|^\(时间："
)

# 句子型/超长昵称的判定阈值
_SENTENCE_LIKE_RE = re.compile(r"[\s，。！？、；：,.!?;:]")
_SENTENCE_LIKE_MAX_LEN = 12


def safe_nickname(nickname: str, user_id: Any = "") -> str:
    """把句子型/超长昵称脱敏为 ``用户<QQ>``，普通昵称保留原样。

    目的：避免昵称内容（如“学费”）进入 LLM 上下文后被当成对话内容。
    """
    nick = (nickname or "").strip()
    if not nick:
        return f"用户{user_id}" if user_id not in (None, "") else "用户"
    if len(nick) > _SENTENCE_LIKE_MAX_LEN or _SENTENCE_LIKE_RE.search(nick):
        return f"用户{user_id}" if user_id not in (None, "") else "用户"
    return nick


def safe_sender_label(sender: str) -> str:
    """把 ``昵称(QQ)`` 形式的发送者标签脱敏为安全标签。

    普通昵称保留 ``昵称(QQ)``；句子型/超长昵称转为 ``用户<QQ>``。
    """
    sender = (sender or "").strip()
    m = re.match(r"^(.*)\((\d+)\)$", sender)
    if m:
        nick, qq = m.group(1), m.group(2)
        safe = safe_nickname(nick, qq)
        if safe == f"用户{qq}":
            return safe
        return f"{safe}({qq})"
    return safe_nickname(sender, "")


def _is_enhanced_context(content: str) -> bool:
    return bool(content and _ENHANCED_RE.search(content))


def _normalize_enhanced_content(content: str) -> str:
    """把旧/新分节增强格式统一归一化为单行 ``昵称(QQ): 正文``。

    旧历史可能是：:
        发送者：X
        发送了：Y

    也可能是新分节：:
        发送者昵称：X
        消息正文：Y

    统一转成：:
        X: Y

    并顺带对发送者做脱敏，避免历史里的句子型昵称继续污染 LLM。
    """
    lines = (content or "").split("\n")
    time_line = ""
    sender_line = ""
    text_line = ""
    meta_lines: list[str] = []

    for line in lines:
        if line.startswith("(时间：") and line.endswith(")"):
            time_line = line
        elif line.startswith("发送者：") or line.startswith("发送者昵称："):
            sender_line = line.split("：", 1)[1] if "：" in line else ""
        elif line.startswith("发送了：") or line.startswith("消息正文："):
            text_line = line.split("：", 1)[1] if "：" in line else ""
        else:
            stripped = line.strip()
            if stripped:
                meta_lines.append(line)

    out: list[str] = []
    if time_line:
        out.append(time_line)
    if sender_line or text_line:
        label = safe_sender_label(sender_line) if sender_line else "用户"
        out.append(f"{label}: {text_line}" if text_line else label)
    out.extend(meta_lines)
    return "\n".join(out) if out else (content or "")


def _mask_enhanced_content(content: str) -> str:
    """对已带增强标记的历史内容做“仅脱敏”，保留原有旧/新/单行格式。

    与 `_normalize_enhanced_content` 不同，本函数不把多行改写成单行，
    只把句子型/超长昵称替换为 `用户<QQ>`，用于在不切换新版格式时防止泄漏。
    """
    lines = (content or "").split("\n")
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            out.append(line)
            continue

        # 旧/新分节：发送者：xxx / 发送者昵称：xxx
        if stripped.startswith("发送者：") or stripped.startswith("发送者昵称："):
            prefix, _, rest = stripped.partition("：")
            out.append(f"{prefix}：{safe_sender_label(rest)}")
            continue

        # 引用了：xxx发送的引用消息：“...”
        m = re.match(r"^(引用了：)(.*?)(发送的引用消息：.*)$", stripped)
        if m:
            out.append(m.group(1) + safe_sender_label(m.group(2)) + m.group(3))
            continue

        # 单行：昵称(QQ): 正文 / 昵称: 正文
        m = re.match(r"^(.+?)(?:\((\d+)\))?: (.*)$", stripped)
        if m:
            label = m.group(1) + (f"({m.group(2)})" if m.group(2) else "")
            out.append(f"{safe_sender_label(label)}: {m.group(3)}")
            continue

        out.append(line)
    return "\n".join(out)


def _time_prefix(ts: Any) -> str:
    """把 unix 时间戳格式化为 ``MM-DD HH:MM `` 前缀；非法/缺失返回空串。"""
    if ts is None:
        return ""
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%m-%d %H:%M ")
    except Exception:
        return ""


def _group_sender_label(
    nickname: str, user_id: Any, include_user_id: bool, mask_nickname: bool = False
) -> str:
    """群聊发送者标签：普通昵称保留；mask_nickname=True 时句子型昵称转为 用户<QQ>。"""
    if mask_nickname:
        nick = safe_nickname(nickname, user_id)
        if nick == f"用户{user_id}" and user_id not in (None, ""):
            return nick
    else:
        nick = (nickname or "").strip()
    parts: list[str] = [nick] if nick else []
    if include_user_id and user_id not in (None, ""):
        if parts:
            parts.append(f"({user_id})")
        else:
            parts.append(str(user_id))
    return "".join(parts) if parts else "用户"


def _segment_text(
    segment: Any,
    at_names: dict[str, str] | None = None,
    mark_unresolved: bool = False,
    message_id: Any = None,
    expanded: "ExpandedRefs | None" = None,
) -> str | None:
    """消息段 → 可读文本。

    Args:
        segment: OneBot 消息段（dict 或对象）。
        at_names: ``{qq: 昵称}`` 映射；命中时把 ``@123`` 渲染成 ``@三哥(123)``
            （复用全局 ``昵称(QQ)`` 约定，不引入新语法）。
        mark_unresolved: 反查失败/未提供时是否输出 ``【未展开:...】`` 标记；
            False 时保持历史的裸 ``@123`` 行为。
        message_id: **承载该消息段的整条消息 id**。合并转发的展开入口要的是这个 id
            （见 ``context_tools``），不是段内 ``data.id``（那是转发节点内部 id，
            超长且超出 int32/JS 安全整数范围）。
        expanded: 已展开登记（本会话取过的 id → 摘要）。命中时把"未展开"升级为
            ``【已展开:… → 摘要】``；@ 对象命中时直接用已知昵称渲染。
    """
    if isinstance(segment, dict):
        stype = segment.get("type", "")
        data = segment.get("data", {}) or {}
    else:
        stype = getattr(segment, "type", "")
        data = getattr(segment, "data", {}) or {}
    if stype == "text":
        text = data.get("text", "")
        return text if text else None
    if stype == "at":
        qq = str(data.get("qq", "") or "")
        if qq in (None, "", "all", "0"):
            return "@所有人"
        name = (at_names or {}).get(qq) or (expanded.user_name(qq) if expanded else "")
        if name:
            return f"@{name}({qq})"
        if mark_unresolved:
            return UNRESOLVED_AT.format(qq=qq)
        return f"@{qq}"
    if stype == "reply":
        if mark_unresolved:
            reply_id = str(data.get("id", "") or "")
            # 没有 id 就无从展开，保持旧占位而不是打一个无法解决的标记
            if reply_id:
                summary = expanded.message_summary(reply_id) if expanded else ""
                if summary:
                    return _expanded_marker("reply", reply_id, summary)
                return UNRESOLVED_REPLY.format(id=reply_id)
        return f"[{_NON_TEXT_SEGMENTS['reply']}]"
    if stype == "forward":
        if mark_unresolved:
            # 优先用整条消息的 id（可直接交给 expand_message → get_forward_msg），
            # 拿不到才退回段内 id
            ref = str(message_id or data.get("id", "") or "")
            if ref:
                summary = expanded.message_summary(ref) if expanded else ""
                if summary:
                    return _expanded_marker("forward", ref, summary)
                return UNRESOLVED_FORWARD_ID.format(id=ref)
            return UNRESOLVED_FORWARD
        return f"[{_NON_TEXT_SEGMENTS['forward']}]"
    if stype in _NON_TEXT_SEGMENTS:
        return f"[{_NON_TEXT_SEGMENTS[stype]}]"
    return None


def extract_msg_text(
    message: Any,
    at_names: dict[str, str] | None = None,
    mark_unresolved: bool = False,
    message_id: Any = None,
    expanded: "ExpandedRefs | None" = None,
) -> str:
    """从 OneBot 消息段中提取可读文本；非文本段用 [图片]/[表情] 之类的占位表示。

    ``at_names`` / ``mark_unresolved`` / ``message_id`` / ``expanded`` 见
    :func:`_segment_text`（``message_id`` 是承载这些段的整条消息 id，合并转发标记会用到它）。
    """
    if isinstance(message, str):
        return message
    if not isinstance(message, list):
        return ""

    parts: list[str] = []
    for seg in message:
        text = _segment_text(seg, at_names, mark_unresolved, message_id, expanded)
        if text:
            parts.append(text)
    return "".join(parts).strip()


def format_online_history(
    messages: list,
    count: int = 50,
    self_ids: set[str] | None = None,
    *,
    include_time: bool = True,
    include_user_id: bool = True,
    max_content: int = 200,
    is_private: bool = False,
    normalize_enhanced: bool = False,
    mask_nickname: bool = False,
    at_names: dict[str, str] | None = None,
    mark_unresolved: bool = False,
    expanded: "ExpandedRefs | None" = None,
) -> str:
    """把 OneBot 消息列表格式化为群聊/私聊背景文本。

    每行形如：``MM-DD HH:MM 昵称(QQ): 内容``（群聊）或 ``MM-DD HH:MM 对方: 内容``（私聊）。
    bot 自己的消息统一打标为「我」，不再跳过——让模型能看到对话两侧、判断“哪些是我说的”。
    消息内容过长时截断，避免背景块把上下文撑爆。

    Args:
        messages: OneBot get_msg_history 返回的消息列表。
        count: 最多保留的最近消息条数。
        self_ids: bot 自己的 QQ 集合，用于把 bot 消息打标为「我」。
        include_time: 是否追加 ``MM-DD HH:MM `` 时间前缀。
        include_user_id: 群聊时是否在昵称后附加 (QQ) 以区分同名。
        max_content: 单条内容最长长度，超出截断。
        is_private: True=私聊（对方只显示为「对方」，不显示昵称/QQ）。
        normalize_enhanced: True=把历史中的旧/新分节增强格式归一化为单行（实验性）。
        mask_nickname: True=对句子型昵称脱敏为 用户<QQ>（实验性）。
        at_names: ``{qq: 昵称}``；把内容里的 ``@123`` 渲染成 ``@三哥(123)``。
        mark_unresolved: 未展开项是否输出 ``【未展开:...】`` 标记（见模块常量注释）。
        expanded: 已展开登记；命中时输出 ``【已展开:... → 摘要】``（状态迁移）。

    Returns:
        格式化后的背景文本（每行一条消息）。
    """
    if not messages:
        return ""
    self_ids = {str(x) for x in (self_ids or set())}
    lines: list[str] = []
    for msg in messages[-count:]:
        if not isinstance(msg, dict):
            continue
        sender = msg.get("sender", {}) or {}
        user_id = sender.get("user_id", "")
        is_self = str(user_id) in self_ids

        # 整条消息 id：合并转发标记要用它（不是转发段内部的 data.id）
        msg_id = msg.get("message_id") or msg.get("real_id") or msg.get("message_seq") or ""
        content = extract_msg_text(msg.get("message"), at_names, mark_unresolved, msg_id, expanded)
        if not content:
            continue
        if len(content) > max_content:
            content = content[:max_content] + "..."

        if is_self:
            label = SELF_TAG
        elif is_private:
            label = PRIVATE_OTHER_TAG
        else:
            nickname = sender.get("card") or sender.get("nickname") or str(user_id) or "未知"
            label = _group_sender_label(nickname, user_id, include_user_id, mask_nickname)

        # 内容已自带“发送者/发送了/时间”自描述（LLM 增强块）时不再套外层前缀。
        # 实验性开启时归一化为单行脱敏；未开启但要求脱敏时只做“仅脱敏”，保留原格式。
        if _is_enhanced_context(content):
            if normalize_enhanced:
                rendered = _normalize_enhanced_content(content)
            elif mask_nickname:
                rendered = _mask_enhanced_content(content)
            else:
                rendered = content
            lines.append(rendered)
            continue

        prefix = _time_prefix(msg.get("time")) if include_time else ""
        lines.append(f"{prefix}{label}: {content}")
    return "\n".join(lines)


def extract_history_messages(result: Any) -> list[dict]:
    """从 OneBot ``get_*_msg_history`` 响应中取出 messages 列表。

    OneBot 响应为完整信封 ``{"status": "ok", "retcode": 0, "data": {"messages": [...]}}``；
    这里同时兼容已被调用方解包的 ``{"messages": [...]}``，以及失败响应（返回空列表）。

    历史回归：此前直接读 ``result["messages"]``，与真实响应结构差一层，导致群聊/私聊
    在线历史永远为空——「群聊环境背景」以及主动发言/定时任务的群背景实际上是静默失效的。
    """
    if not isinstance(result, dict):
        return []
    data = result.get("data")
    if isinstance(data, dict) and data.get("messages") is not None:
        return list(data.get("messages") or [])
    messages = result.get("messages")
    return list(messages) if isinstance(messages, list) else []


def collect_at_ids(messages: list) -> list[str]:
    """收集一批 OneBot 消息里出现的 @ 对象 QQ（去重、保序、跳过全体/机器人无关项）。"""
    seen: list[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        for seg in msg.get("message") or []:
            if isinstance(seg, dict):
                stype, data = seg.get("type", ""), seg.get("data", {}) or {}
            else:
                stype, data = getattr(seg, "type", ""), getattr(seg, "data", {}) or {}
            if stype != "at":
                continue
            qq = str(data.get("qq", "") or "")
            if not qq or qq in ("all", "0") or qq in seen:
                continue
            seen.append(qq)
    return seen


async def fetch_group_online_history(
    bot: Any,
    group_id: Any,
    count: int = 50,
    self_ids: set[str] | None = None,
    *,
    normalize_enhanced: bool = False,
    mask_nickname: bool = False,
    resolve_at: bool = True,
    mark_unresolved: bool = False,
    bot_id: Any = "",
    session_id: Any = "",
    expanded: "ExpandedRefs | None" = None,
) -> str:
    """拉取群聊最近消息，格式化为带发送者/时间/QQ 的背景文本。

    ``resolve_at=True`` 时把记录里的 ``@123`` 预展开成 ``@三哥(123)``——这是"补全消息
    环境"里**成本最低的一刀**：QQ 群里的 @ 是高频骨架，而反查能力（含缓存）本来就有。
    反查失败（无权限/已退群/连接异常）在 ``mark_unresolved=True`` 时输出
    ``【未展开:用户123】``，让模型知道"这里缺东西"而不是把裸 id 当正文猜。

    ``expanded`` 给定时，本会话已取回过的 id 渲染成 ``【已展开:… → 摘要】``；
    不传时自动按 ``bot_id``/``session_id`` 查焦点表（调用方通常只想传这两个）。
    """
    try:
        result = await bot.get_msg_history(
            group_id=int(group_id),
            user_id=0,
            count=count,
            reverse_order=False,
        )
        messages = extract_history_messages(result)
        if not messages:
            return ""

        at_names: dict[str, str] = {}
        if resolve_at:
            from app.llm.nicknames import resolve_nicknames

            window = [m for m in messages[-count:] if isinstance(m, dict)]
            at_names = await resolve_nicknames(
                bot, group_id, collect_at_ids(window), bot_id=bot_id
            )

        refs = expanded
        if refs is None and session_id not in (None, ""):
            refs = lookup_expanded(bot_id, session_id)

        return format_online_history(
            messages,
            count,
            self_ids=self_ids,
            normalize_enhanced=normalize_enhanced,
            mask_nickname=mask_nickname,
            at_names=at_names,
            mark_unresolved=mark_unresolved,
            expanded=refs,
        )
    except Exception as e:
        # 背景块取不到时退回空串（调用方本来就把空块当作“无背景”），但必须留痕
        logger.debug(f"[GroupContext] 拉取群 {group_id} 在线历史失败（已忽略）: {e}")
        return ""


async def fetch_private_online_history(
    bot: Any,
    user_id: Any,
    count: int = 50,
    self_ids: set[str] | None = None,
    *,
    bot_id: Any = "",
    session_id: Any = "",
) -> str:
    """拉取私聊最近消息，格式化为不带昵称的私聊背景文本（我方=我、对方=对方）。"""
    try:
        result = await bot.get_msg_history(
            group_id=0,
            user_id=int(user_id),
            count=count,
            reverse_order=False,
        )
        messages = extract_history_messages(result)
        if not messages:
            return ""
        refs = lookup_expanded(bot_id, session_id) if session_id not in (None, "") else None
        return format_online_history(
            messages, count, self_ids=self_ids, is_private=True, expanded=refs
        )
    except Exception as e:
        # 背景块取不到时退回空串（调用方把空块当作“无背景”），但必须留痕
        logger.debug(f"[GroupContext] 拉取私聊 {user_id} 在线历史失败（已忽略）: {e}")
        return ""


async def fetch_group_name(bot: Any, group_id: Any) -> str:
    """获取群名；失败时返回空字符串。"""
    try:
        result = await bot.get_group_info(group_id=int(group_id))
        data = (result or {}).get("data", {}) or {}
        return str(data.get("group_name", "") or "")
    except Exception:
        return ""


def format_history_for_llm(
    history: list[dict],
    is_private: bool = False,
    *,
    normalize_enhanced: bool = False,
    mask_nickname: bool = False,
) -> list[dict]:
    """把带发送者元数据的会话历史渲染成纯文本消息，避免多余字段进入 API。

    打标方式（与在线历史一致）：
    - 群聊：``MM-DD HH:MM 昵称(QQ): 内容``；
    - 私聊：``MM-DD HH:MM 对方: 内容``（私聊不需要昵称）；
    - bot 自己的回复（assistant）不加任何「时间/我: 」前缀，直接返回原文，
      避免模型模仿“MM-DD HH:MM 我: ”的格式，把该前缀也写进回复内容从而污染历史。

    Args:
        history: 会话历史条目（role/content/nickname/user_id/time 等字段）。
        is_private: True=私聊模式（对方不显示昵称，只显示「对方」）。
        normalize_enhanced: True=把历史中的旧/新分节增强格式归一化为单行（实验性）。
        mask_nickname: True=对句子型昵称脱敏为 用户<QQ>（实验性）。

    Returns:
        OpenAI messages 风格的历史列表，content 已渲染为打标文本。
    """
    result = []
    for m in history:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "assistant":
            # 模型自己的回复不再加“时间+我: ”前缀：避免模型模仿该格式，
            # 把“MM-DD HH:MM 我: ”也写进回复内容（会污染历史并自我强化）。
            result.append({"role": role, "content": content})
            continue
        # 内容已自带“发送者/发送了/时间”自描述（LLM 增强块）时不再套外层前缀。
        # 实验性开启时归一化为单行脱敏；未开启但要求脱敏时只做“仅脱敏”，保留原格式。
        if _is_enhanced_context(content):
            if normalize_enhanced:
                rendered = _normalize_enhanced_content(content)
            elif mask_nickname:
                rendered = _mask_enhanced_content(content)
            else:
                rendered = content
            result.append({"role": role, "content": rendered})
            continue
        if is_private:
            sender = PRIVATE_OTHER_TAG
        else:
            nickname = m.get("nickname") or ""
            user_id = m.get("user_id") or ""
            sender = _group_sender_label(nickname, user_id, include_user_id=True, mask_nickname=mask_nickname)
        rendered = f"{_time_prefix(m.get('time'))}{sender}: {content}"
        result.append({"role": role, "content": rendered})
    return result


def build_group_env_text(
    *,
    group_id: Any,
    group_name: str = "",
    history_text: str = "",
    current_time: str | None = None,
) -> str:
    """组装群聊环境 system 背景块。

    只有 history_text 非空时才附加“最近群聊记录”小节；
    如果调用方不想要任何背景，可以直接不调用本函数。

    记录里存在未展开内容时，末尾追加一行缺口摘要（见 :func:`unresolved_summary`）：
    让模型不必自己逐行扫描就知道缺什么。
    """
    lines: list[str] = []
    if group_name:
        lines.append(f"群名：{group_name}")
    lines.append(f"群号：{group_id}")
    if current_time is None:
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines.append(f"当前时间：{current_time}")
    if history_text:
        lines.append("最近群聊记录：")
        lines.append(history_text)
        summary = unresolved_summary(history_text)
        if summary:
            lines.append(summary)
    return "\n".join(lines)