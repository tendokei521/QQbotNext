"""结构化消息的「解析约定」段（L1，order 60）。

格式自解释（`[time=..][sender:..]正文[at:..][reply_to:..]`），这里只给模型最必要的
几条读法，避免回到"解释旧格式"的大段文案。
"""

from __future__ import annotations

from typing import Any

CONVENTION = """### 消息格式约定
本轮与历史消息都是用方括号字段标记的结构化文本：
- `[time=..]` `[group_id=..]` `[sender:id=..,name=..]` 是元信息，不是对方说的话；
- 正文里的 `\\[` 是转义后的普通方括号，不是标记；
- `[at:id=..]` `[reply_to:..]` 表示 @ 与引用；`[reply_to:...,text="..."]` 里的 text 是
  被引用消息的内容（里面可能还有占位符）；
- `[image:id=..]` `[forward_message:id=..]` `[record:id=..]` 等是不可内联内容的占位符：
  需要内容时用对应工具展开（合并转发用 get_forward_message，其余用 expand_message）；
  取不到就说取不到，不要猜。"""


def _cfg(req: Any, key: str, default: Any):
    getter = getattr(getattr(req, "config", None), "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except Exception:  # noqa: BLE001
            return default
    return default


def message_meta_section(req: Any) -> list[dict]:
    if not bool(_cfg(req, "structured_assembly", True)):
        return []
    return [{"role": "system", "content": CONVENTION}]


# 兼容：框架内置段统一用 convention_section 这个名字
convention_section = message_meta_section
