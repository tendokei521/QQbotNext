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
from datetime import datetime
from typing import Any

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

# bot 自己的固定标签（群聊/私聊一致）；私聊对方显示“对方(QQ)”，便于工具调用与身份区分
SELF_TAG = "我"
PRIVATE_OTHER_TAG = "对方"


def private_other_label(user_id: Any = "") -> str:
    """私聊对方标签：优先显示 QQ，方便 LLM 在工具调用中拿到对方账号。"""
    uid = str(user_id or "").strip()
    if uid:
        return f"{PRIVATE_OTHER_TAG}({uid})"
    return PRIVATE_OTHER_TAG

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


def _segment_text(segment: Any) -> str | None:
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
        qq = data.get("qq", "")
        return f"@{qq}" if qq not in (None, "", "all") else "@所有人"
    if stype in _NON_TEXT_SEGMENTS:
        return f"[{_NON_TEXT_SEGMENTS[stype]}]"
    return None


def extract_msg_text(message: Any) -> str:
    """从 OneBot 消息段中提取可读文本；非文本段用 [图片]/[表情] 之类的占位表示。"""
    if isinstance(message, str):
        return message
    if not isinstance(message, list):
        return ""

    parts: list[str] = []
    for seg in message:
        text = _segment_text(seg)
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

        content = extract_msg_text(msg.get("message"))
        if not content:
            continue
        if len(content) > max_content:
            content = content[:max_content] + "..."

        if is_self:
            label = SELF_TAG
        elif is_private:
            label = private_other_label(user_id)
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


async def fetch_group_online_history(
    bot: Any,
    group_id: Any,
    count: int = 50,
    self_ids: set[str] | None = None,
    *,
    normalize_enhanced: bool = False,
    mask_nickname: bool = False,
) -> str:
    """拉取群聊最近消息，格式化为带发送者/时间/QQ 的背景文本。"""
    try:
        result = await bot.get_msg_history(
            group_id=int(group_id),
            user_id=0,
            count=count,
            reverse_order=False,
        )
        if not result or not isinstance(result, dict):
            return ""
        messages = result.get("messages", []) or []
        return format_online_history(
            messages,
            count,
            self_ids=self_ids,
            normalize_enhanced=normalize_enhanced,
            mask_nickname=mask_nickname,
        )
    except Exception:
        return ""


async def fetch_private_online_history(
    bot: Any,
    user_id: Any,
    count: int = 50,
    self_ids: set[str] | None = None,
) -> str:
    """拉取私聊最近消息，格式化为不带昵称的私聊背景文本（我方=我、对方=对方）。"""
    try:
        result = await bot.get_msg_history(
            group_id=0,
            user_id=int(user_id),
            count=count,
            reverse_order=False,
        )
        if not result or not isinstance(result, dict):
            return ""
        messages = result.get("messages", []) or []
        return format_online_history(messages, count, self_ids=self_ids, is_private=True)
    except Exception:
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
            sender = private_other_label(m.get("user_id") or "")
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
    return "\n".join(lines)