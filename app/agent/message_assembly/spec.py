"""消息组装规范（重写版唯一事实来源）。

一条消息 = 头部标记 + 发送者标记 + 内容流 + 尾部标记：

    [time=2026-08-13 11:20,message_id=123456]
    [sender:id=20002,name="小明"]
    这题怎么做[at:id=10002,name="小红"]你看得懂吗[forward_message:id=9f3c2a]
    [reply_to:id=456,name="小红",text="昨晚那部剧结局真离谱",message_id=456]

规则：
1. 字段顺序固定（time → message_id → sender → 内容流 → reply）——同输入必然同字节；
   群信息（group_id/group_name）不逐条出现，统一放在 L1 system 尾部的「当前群聊」一行；
2. 正文里的 ``[`` 转义为 ``\\[``，结构化标记只由本模块产生（不可伪造）；
3. 不可内联的内容一律占位符（见 ``PLACEHOLDERS``），由工具按需展开；
4. 时间统一 ``年-月-日 时:分``（本地时区）。
"""

from __future__ import annotations

import json
import time as _time
from typing import Any

#: 非文本段 → 占位符名（工具契约与本表一一对应）
PLACEHOLDERS: dict[str, str] = {
    "image": "image",
    "mface": "image",
    "face": "face",
    "record": "record",
    "video": "video",
    "file": "file",
    "json": "json_card",
    "xml": "xml_card",
    "markdown": "markdown",
    "forward": "forward_message",
}

#: 需要工具展开的占位符 → 工具名（`None` = 无需展开）
EXPANDERS: dict[str, str | None] = {
    "image": "expand_message",
    "forward_message": "get_forward_message",
    "record": "expand_message",
    "video": "expand_message",
    "file": "expand_message",
    "json_card": "expand_message",
    "xml_card": "expand_message",
    "at": None,
    "face": None,
    "reply_to": "expand_message",
}

TIME_FORMAT = "%Y-%m-%d %H:%M"


def format_time(ts: Any = None) -> str:
    """统一时间：``2026-08-13 11:20``。"""
    try:
        return _time.strftime(TIME_FORMAT, _time.localtime(int(ts) if ts else None))
    except (TypeError, ValueError, OSError):
        return _time.strftime(TIME_FORMAT)


def quote(value: Any) -> str:
    """字段值按 JSON 字符串写（引号/换行自动转义）。"""
    return json.dumps("" if value is None else str(value), ensure_ascii=False)


def escape_body(text: Any) -> str:
    """正文转义：``[`` → ``\\[``，防止用户伪造结构化标记。"""
    return str(text or "").replace("[", "\\[")


def field(name: str, value: Any, *, quoted: bool = False) -> str:
    if value in (None, ""):
        return ""
    return f"{name}={quote(value) if quoted else value}"


def marker(tag: str, *parts: str) -> str:
    """拼一个 ``[tag:field,field]`` 标记（parts 里为空的字段自动丢弃）。"""
    kept = [p for p in parts if p]
    return f"[{tag}:{','.join(kept)}]" if kept else f"[{tag}]"
