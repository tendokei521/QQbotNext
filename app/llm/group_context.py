"""群聊环境上下文构建（不改动“非 @ 不入会话历史”的现有策略）。

用途：
- 在 `include_pre_history` 开启时，把群聊最近在线消息格式化为“带发送者、时间、QQ”的背景文本；
- 组装群名 / 群号 / 当前时间 / 最近记录等 system 提示块，供普通对话、主动消息、定时任务复用。
"""

from __future__ import annotations

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
) -> str:
    """把 OneBot 消息列表格式化为群聊/私聊背景文本。

    每行形如：``[10:23] 昵称(10001): 消息内容``。
    消息内容过长时截断，避免背景块把上下文撑爆。
    """
    if not messages:
        return ""
    self_ids = self_ids or set()
    lines: list[str] = []
    for msg in messages[-count:]:
        if not isinstance(msg, dict):
            continue
        sender = msg.get("sender", {}) or {}
        user_id = sender.get("user_id", "")
        if user_id is not None and str(user_id) in self_ids:
            continue
        nickname = sender.get("card") or sender.get("nickname") or str(user_id) or "未知"
        content = extract_msg_text(msg.get("message"))
        if not content:
            continue
        if len(content) > max_content:
            content = content[:max_content] + "..."

        prefix = ""
        if include_time:
            ts = msg.get("time")
            if ts:
                try:
                    prefix += datetime.fromtimestamp(int(ts)).strftime("[%m-%d %H:%M] ")
                except Exception:
                    pass
        if include_user_id and user_id not in (None, ""):
            nickname = f"{nickname}({user_id})"
        lines.append(f"{prefix}{nickname}: {content}")
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
    """拉取私聊最近消息，格式化为带发送者/时间/QQ 的背景文本。"""
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
        return format_online_history(messages, count, self_ids=self_ids)
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

    群聊格式：``[08-18 12:30] 昵称(QQ): 内容``
    私聊格式：``[08-18 12:30] 昵称: 内容``
    """
    result = []
    for m in history:
        content = m.get("content", "")
        if m.get("role") == "assistant":
            rendered = content
        else:
            nickname = m.get("nickname") or ""
            user_id = m.get("user_id") or ""
            if is_private:
                sender = nickname or (f"用户({user_id})" if user_id else "用户")
            else:
                if nickname and user_id:
                    sender = f"{nickname}({user_id})"
                elif nickname:
                    sender = nickname
                elif user_id:
                    sender = f"用户({user_id})"
                else:
                    sender = "用户"
            ts = m.get("time")
            time_prefix = ""
            if ts:
                try:
                    time_prefix = datetime.fromtimestamp(int(ts)).strftime("[%m-%d %H:%M] ")
                except Exception:
                    pass
            rendered = f"{time_prefix}{sender}: {content}"
        result.append({"role": m["role"], "content": rendered})
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