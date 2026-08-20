"""
标签剥离（防御性）。

tag 状态系统已废弃（废案），仅保留「剥离任意 <type=xxx> 标签」的能力，
防止模型按角色提示词输出标签时漏到客户端。
"""

from __future__ import annotations

import re

# 通用标签清洗：剥掉任意 <type=xxx>...</type> 块 / 孤立开标签（含中文类型名）
_TAG_BLOCK_RE = re.compile(r"<type\s*=\s*[\w一-鿿]+>.*?</type>", re.DOTALL)
_TAG_OPEN_RE = re.compile(r"<type\s*=\s*[\w一-鿿]+>")

# 括号块：半角 (...) 与全角 （…）。要求块内至少有非空白内容，避免反向误伤
# （例如嵌套剥完留下 "()" 时不再继续剥而清空整句）。
_PAREN_BLOCK_RE = re.compile(r"（[^（）]*[^\s（）][^（）]*）|\([^()]*[^\s()][^()]*\)")
# 剥除后可能残留的悬空分隔符/波浪号（如 "好的～（笑）" → "好的～" → 清掉尾部的～或、，）
_DANGLING_TAIL_RE = re.compile(r"[、，,～~]\s*$")
_MULTI_SPACE_RE = re.compile(r"[ \t\u3000]{2,}")


def strip_all_tags(text: str) -> str:
    """剥离所有 <type=...> 标签（无论类型名），并折叠剥离后残留的空行。"""
    if not text:
        return text
    text = _TAG_BLOCK_RE.sub("", text)
    text = _TAG_OPEN_RE.sub("", text)
    lines = [line for line in text.split("\n") if line.strip()]
    return "\n".join(lines).strip()


def strip_parentheses(text: str, max_rounds: int = 4) -> str:
    """剥离全/半角括号内容（如 （笑）、（备注）、(...)），防止括号风格被后续模仿。

    - 只剥括号**块**，成对未闭合的不剥（避免吞掉正文）；
    - 循环收敛最多 max_rounds 次，处理少量嵌套；
    - 剥除后清理悬空尾部分隔符（、，,～~）并折叠连续空格；
    - **清洗结果为空时保留原文**：避免整段纯括号内容被清空并误触发兜底回复。
    """
    if not text:
        return text
    out = str(text)
    for _ in range(max_rounds):
        new = _PAREN_BLOCK_RE.sub("", out)
        if new == out:
            break
        out = new
    # 修复剥除留下的脏尾/脏空白
    cleaned = _TRAIL_DANGLING_AND_SPACE(out)
    if not cleaned.strip():
        return str(text)
    return cleaned


def maybe_strip_parentheses(text: str, enabled: bool = True) -> str:
    """按开关决定是否剥括号（写入历史时用）。"""
    if not enabled:
        return text
    return strip_parentheses(text)


def _TRAIL_DANGLING_AND_SPACE(text: str) -> str:
    out = _DANGLING_TAIL_RE.sub("", text)
    # 若整段只剩分隔符/空白则返回空
    if not out.strip(" 、，,～~"):
        return ""
    out = _MULTI_SPACE_RE.sub(" ", out)
    return out.strip()
