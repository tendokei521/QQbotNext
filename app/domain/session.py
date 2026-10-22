"""会话 id 的唯一解析/构造口径。

全库此前把 ``group_123`` / ``private_456`` 的前缀解析抄了 5 遍
（`proactivity/manager.py`、`proactivity/stream.py`、`proactivity/scheduler.py`、
`loop/pipeline.py`、以及各处 ``f"group_{gid}"`` 拼装）。

统一到这里，顺带消掉一个隐患：``int()`` 转换散落各处，遇到非数字 target 会在
发送前才炸，现在集中在一处并给出明确异常。
"""

from __future__ import annotations

from typing import Any


class SessionIdError(ValueError):
    """会话 id 非法（无法解析出目标）。"""


def parse_session_id(session_id: Any) -> tuple[bool, str]:
    """把会话 id 解析成 ``(is_group, target)``。

    Args:
        session_id: ``group_<群号>`` / ``private_<QQ>``；无前缀时按私聊处理（兼容旧调用）。

    Returns:
        ``(is_group, target)``，``target`` 为字符串（不去空格以外做任何转换）。

    Raises:
        SessionIdError: 空 id。

    Examples:
        >>> parse_session_id("group_123")
        (True, '123')
        >>> parse_session_id("private_456")
        (False, '456')
        >>> parse_session_id("789")
        (False, '789')
    """
    raw = str(session_id or "").strip()
    if not raw:
        raise SessionIdError("会话 id 为空")
    if raw.startswith("group_"):
        return True, raw[len("group_") :]
    if raw.startswith("private_"):
        return False, raw[len("private_") :]
    return False, raw


def make_session_id(is_group: bool, target: Any) -> str:
    """按方向拼会话 id：``make_session_id(True, 123) -> "group_123"``。"""
    prefix = "group" if is_group else "private"
    return f"{prefix}_{str(target).strip()}"


def try_parse_session_id(session_id: Any) -> tuple[bool, str] | None:
    """解析失败返回 None（给"读不懂就跳过"的调用方用，避免到处 try/except）。"""
    try:
        return parse_session_id(session_id)
    except SessionIdError:
        return None
