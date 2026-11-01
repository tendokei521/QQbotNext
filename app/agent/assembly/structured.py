"""结构化消息渲染（重构雏形）：把消息段渲染成内联字段标记。

目标格式（时间/群/发送者/正文与 at 按顺序/引用在尾部）：

    [time=12:34,group_id=466052056,group_name="测试群"][sender:id=20002,name="小明"]
    这题怎么做[at:id=10002,name="小红"]你看得懂吗[forward_message:id=9f3c2a]
    [reply_to:id=456,name="小红",text="昨晚那部剧结局真离谱",message_id=456]

约定：
- 正文里的 ``[`` 转义成 ``\\[``，结构化标记只能由本渲染器产生（防伪造）；
- 不可内联的内容一律给占位符（``[image:id=..]`` / ``[forward_message:id=..]`` …），
  由工具按需展开，模型不要猜内容；
- 字段顺序固定（时间→群→发送者→内容流→引用），便于前缀缓存。
"""

from __future__ import annotations

import json
from typing import Any

#: 非文本段 → 占位符名（文档/工具契约共同引用这一份）
PLACEHOLDER_KINDS: dict[str, str] = {
    "image": "image",
    "face": "face",
    "mface": "mface",
    "record": "record",
    "video": "video",
    "file": "file",
    "json": "json_card",
    "xml": "xml_card",
    "forward": "forward_message",
    "markdown": "markdown",
}


def _q(value: Any) -> str:
    """字段值统一按 JSON 字符串写（引号/换行自动转义）。"""
    return json.dumps("" if value is None else str(value), ensure_ascii=False)


def escape_text(text: Any) -> str:
    """正文转义：``[`` → ``\\[``，避免用户正文伪造结构化标记。"""
    return str(text or "").replace("[", "\\[")


def _segments(message: Any) -> list[Any]:
    return list(message or [])


def _segment_id(data: dict) -> str:
    for key in ("file", "url", "id", "path", "fid"):
        value = data.get(key)
        if value:
            return str(value)
    return ""


def render_content(message: Any) -> str:
    """内容流：文本原样（转义），at/图片/表情/转发等按段顺序内联为标记。"""
    parts: list[str] = []
    for seg in _segments(message):
        stype = str(getattr(seg, "type", "") or "")
        data = dict(getattr(seg, "data", None) or {})
        if stype == "text":
            parts.append(escape_text(data.get("text", "")))
        elif stype == "at":
            qq = str(data.get("qq", "") or "")
            if qq in ("all", "0"):
                parts.append("[at:id=all,name=\"全体成员\"]")
            elif qq:
                name = data.get("name") or data.get("card") or ""
                parts.append(f"[at:id={qq},name={_q(name)}]" if name else f"[at:id={qq}]")
        elif stype == "reply":
            continue  # 引用统一放尾部（reply_to）
        elif stype in PLACEHOLDER_KINDS:
            ident = _segment_id(data)
            kind = PLACEHOLDER_KINDS[stype]
            parts.append(f"[{kind}:id={ident}]" if ident else f"[{kind}]")
        else:
            parts.append(f"[{stype}]")
    return "".join(parts)


def render_turn(
    *,
    message: Any,
    time_str: str,
    sender_id: Any,
    sender_name: str = "",
    group_id: Any = None,
    group_name: str = "",
    reply: dict | None = None,
) -> str:
    """渲染一条消息（本轮或历史条目）。

    ``reply`` 形如 ``{"id": 456, "message_id": 456, "name": "小红", "text": "…"}``：
    ``text`` 里允许嵌套占位符（如 ``[forward_message:id=…]``）。
    """
    head: list[str] = [f"time={time_str}"]
    if group_id not in (None, "", 0, "0"):
        head.append(f"group_id={group_id}")
        if group_name:
            head.append(f"group_name={_q(group_name)}")
    out = ["[" + ",".join(head) + "]"]
    name = str(sender_name or "").strip()
    if name:
        out.append(f"[sender:id={sender_id},name={_q(name)}]")
    else:
        out.append(f"[sender:id={sender_id}]")
    out.append(render_content(message))
    if reply and (reply.get("text") or reply.get("id")):
        fields = [f"id={reply.get('id') or ''}"]
        if reply.get("name"):
            fields.append(f"name={_q(reply['name'])}")
        if reply.get("text"):
            fields.append(f"text={_q(reply['text'])}")
        if reply.get("message_id"):
            fields.append(f"message_id={reply['message_id']}")
        out.append("[reply_to:" + ",".join(fields) + "]")
    return "".join(out)
