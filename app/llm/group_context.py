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

# bot 自己的固定标签（群聊/私聊一致）；私聊对方用“对方”，不展示昵称
SELF_TAG = "我"
PRIVATE_OTHER_TAG = "对方"

# 已自带“发送者/发送了/时间”自描述内容（LLM 增强模块 llm_enhance 产出的散文块）。
# 这类内容再套外层“MM-DD HH:MM 昵称(QQ):”会变成重复脏信息，渲染时应原样输出。
_ENHANCED_RE = re.compile(r"(?:^|\n)发送了：|^\(时间：")


def _is_enhanced_context(content: str) -> bool:
    return bool(content and _ENHANCED_RE.search(content))


def _time_prefix(ts: Any) -> str:
    """把 unix 时间戳格式化为 ``MM-DD HH:MM `` 前缀；非法/缺失返回空串。"""
    if ts is None:
        return ""
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%m-%d %H:%M ")
    except Exception:
        return ""


def _group_sender_label(nickname: str, user_id: Any, include_user_id: bool) -> str:
    """群聊发送者标签：昵称为主，昵称缺失时回退 QQ，必要时附 (QQ) 区分同名。"""
    parts: list[str] = []
    if nickname:
        parts.append(nickname)
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
            label = PRIVATE_OTHER_TAG
        else:
            nickname = sender.get("card") or sender.get("nickname") or str(user_id) or "未知"
            label = _group_sender_label(nickname, user_id, include_user_id)

        # 内容已自带“发送者/发送了/时间”自描述（LLM 增强块）时不再套外层前缀，避免重复脏信息
        if _is_enhanced_context(content):
            lines.append(content)
            continue

        prefix = _time_prefix(msg.get("time")) if include_time else ""
        lines.append(f"{prefix}{label}: {content}")
    return "\n".join(lines)


async def fetch_group_online_history(
    bot: Any,
    group_id: Any,
    count: int = 50,
    self_ids: set[str] | None = None,
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
        return format_online_history(messages, count, self_ids=self_ids)
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


def format_history_for_llm(history: list[dict], is_private: bool = False) -> list[dict]:
    """把带发送者元数据的会话历史渲染成纯文本消息，避免多余字段进入 API。

    打标方式（与在线历史一致）：
    - 群聊：``MM-DD HH:MM 昵称(QQ): 内容``；
    - 私聊：``MM-DD HH:MM 对方: 内容``（私聊不需要昵称）；
    - bot 自己的回复（assistant）不加任何「时间/我: 」前缀，直接返回原文，
      避免模型模仿“MM-DD HH:MM 我: ”的格式，把该前缀也写进回复内容从而污染历史。

    Args:
        history: 会话历史条目（role/content/nickname/user_id/time 等字段）。
        is_private: True=私聊模式（对方不显示昵称，只显示「对方」）。

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
        # 内容已自带“发送者/发送了/时间”自描述（LLM 增强块）时不再套外层前缀，
        # 避免同一句出现两份“时间/发送者”的重复脏信息。
        if _is_enhanced_context(content):
            result.append({"role": role, "content": content})
            continue
        if is_private:
            sender = PRIVATE_OTHER_TAG
        else:
            nickname = m.get("nickname") or ""
            user_id = m.get("user_id") or ""
            sender = _group_sender_label(nickname, user_id, include_user_id=True)
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