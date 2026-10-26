"""提示词前缀稳定性工具（KV cache / prompt caching 的可测基础）。

## 为什么需要

OpenAI / DeepSeek 的**自动**前缀缓存与 Anthropic 的**显式**缓存命中口径相同：
从第一条消息的第一个字节开始，逐字节相同的部分才会被复用。提示词里只要混进
时间戳、随机数、集合遍历顺序或 ``dict`` 插入顺序，前缀就会在无意义的地方变化，
缓存整段落空——用户看到的是钱照花、首字延迟不降。

本模块只提供三个**纯函数**，不读配置、不打日志、不依赖运行时，便于在组装层
与测试里随时取指纹：

- :func:`system_prefix_fingerprint` —— 只看 system 段，给出稳定短指纹；
- :func:`stable_messages_digest` —— 给整份 messages 一个稳定摘要（回归快照用）；
- :func:`is_prefix_stable` —— 判断新 messages 是否只是在尾部追加。

## 约定（改实现前先读）

- 文本统一按 **UTF-8** 编码再哈希；输出为**小写十六进制前 16 位**（64 bit，
  用于契约快照足够，不是安全用途）；
- 指纹里**不含**时间戳、随机数、``id()``、``set`` 迭代顺序，因此同一输入多次
  调用必然同值；Python 的 ``hash()`` 随机化也影响不到这里（只对字节做 sha256）；
- system 段按 messages 里的出现顺序拼接，段之间用固定的 ``"\\n\\n"``，空段跳过；
  固定分隔符只为让「两段拼成一段」与「一段里含换行」得到不同指纹；
- 段内容支持两种形态：``str``，或 OpenAI 风格的内容块数组。块数组按顺序**直接
  拼接**（不额外插分隔符）：``text`` / ``input_text`` 块取 ``text`` 字段，其它块
  按 **key 排序的紧凑 JSON** 参与哈希，保证同一语义块永远同一字节；
- :func:`stable_messages_digest` 使用
  ``json.dumps(..., sort_keys=True, ensure_ascii=False, separators=(",", ":"),
  default=str)``：``sort_keys`` 消除 dict 插入顺序；``ensure_ascii=False`` 让中文
  按原字符参与编码（两种写法只要前后一致都不会误报，这里选与落盘文本一致的写法）；
  ``default=str`` 只兜底标准消息里不该出现的非 JSON 对象（如 ``Path``），标准
  消息不受影响；
- :func:`is_prefix_stable` 逐条比较 ``role`` 与 ``content``（``content`` 按值比较，
  dict 的 key 顺序不影响结果），其余字段（例如缓存的 ``time`` 元信息）不参与。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

#: 指纹/摘要取 sha256 十六进制的前多少位
_FINGERPRINT_CHARS = 16

#: system 段之间的固定分隔符（见模块 docstring「约定」）
_SYSTEM_JOINER = "\n\n"

#: 稳定 JSON 的固定分隔符（紧凑形态，避免空格带来的方言差异）
_JSON_SEPARATORS = (",", ":")


def _sha256_hex16(text: str) -> str:
    """字符串按 UTF-8 编码后取 sha256 前 16 位（小写十六进制）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:_FINGERPRINT_CHARS]


def _part_text(part: Any) -> str:
    """内容块 → 参与哈希的文本（text 块取 text，其它块取稳定 JSON）。"""
    if isinstance(part, dict):
        part_type = str(part.get("type", "") or "").lower()
        if part_type in ("text", "input_text"):
            return str(part.get("text", "") or "")
        return json.dumps(
            part,
            sort_keys=True,
            ensure_ascii=False,
            separators=_JSON_SEPARATORS,
            default=str,
        )
    return str(part)


def _content_text(content: Any) -> str:
    """把一条消息的 ``content`` 归一成参与哈希的文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(_part_text(part) for part in content)
    if content is None:
        return ""
    return str(content)


def system_prefix_fingerprint(messages: list[dict] | None) -> str:
    """所有 system 段按序拼接后的 sha256 短指纹。

    只统计 ``role == "system"`` 的消息；空段跳过。同一输入多次调用结果相同，
    改动任意一个字符（或调换两段顺序）都会改变指纹。
    """
    parts: list[str] = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        if str(message.get("role", "") or "") != "system":
            continue
        text = _content_text(message.get("content", ""))
        if text:
            parts.append(text)
    return _sha256_hex16(_SYSTEM_JOINER.join(parts))


def stable_messages_digest(messages: list[dict] | None) -> str:
    """全量 messages 的稳定摘要（与 dict 插入顺序无关）。

    用于「同一输入必然同一摘要」的回归快照：调用两次同值，改动任意一条消息的
    任意内容即变值。
    """
    serialized = json.dumps(
        list(messages or []),
        sort_keys=True,
        ensure_ascii=False,
        separators=_JSON_SEPARATORS,
        default=str,
    )
    return _sha256_hex16(serialized)


def _message_identity(message: Any) -> tuple[str, Any]:
    """一条消息参与前缀比较的身份：``(role, content)``。"""
    if not isinstance(message, dict):
        return ("", message)
    return (str(message.get("role", "") or ""), message.get("content"))


def is_prefix_stable(prev: list[dict] | None, cur: list[dict] | None) -> bool:
    """``cur`` 的前 ``len(prev)`` 条是否与 ``prev`` 逐条完全一致（role + content）。

    语义：**通过 = 可以在缓存前缀上继续追加**。因此：

    - ``prev`` 为空 → 恒为 ``True``（没有历史前缀可言）；
    - 尾部追加新消息 → ``True``；
    - 改动任意一条已有消息 / 调换顺序 → ``False``；
    - ``cur`` 比 ``prev`` 短（团缩/截断）→ ``False``。
    """
    previous = list(prev or [])
    current = list(cur or [])
    if len(current) < len(previous):
        return False
    for old, new in zip(previous, current, strict=False):
        if _message_identity(old) != _message_identity(new):
            return False
    return True


__all__ = [
    "is_prefix_stable",
    "stable_messages_digest",
    "system_prefix_fingerprint",
]
