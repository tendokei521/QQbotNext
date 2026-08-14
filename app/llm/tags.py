"""
标签剥离（防御性）。

tag 状态系统已废弃（废案），仅保留「剥离任意 <type=xxx> 标签」的能力，
防止模型按角色提示词输出标签时漏到客户端。
"""

from __future__ import annotations

import re
from typing import Optional

# 通用标签清洗：剥掉任意 <type=xxx>...</type> 块 / 孤立开标签（含中文类型名）
_TAG_BLOCK_RE = re.compile(r"<type\s*=\s*[\w一-鿿]+>.*?</type>", re.DOTALL)
_TAG_OPEN_RE = re.compile(r"<type\s*=\s*[\w一-鿿]+>")


def strip_all_tags(text: str) -> str:
    """剥离所有 <type=...> 标签（无论类型名），并折叠剥离后残留的空行。"""
    if not text:
        return text
    text = _TAG_BLOCK_RE.sub("", text)
    text = _TAG_OPEN_RE.sub("", text)
    lines = [line for line in text.split("\n") if line.strip()]
    return "\n".join(lines).strip()
