"""ID 归一：OneBot 用数字、官方 QQ 机器人用字符串 openid。

领域层同时接受 ``int`` 与 ``str``：

- OneBot 的 ``user_id/group_id/message_id`` 保持数字（协议端与既有测试都按数字）；
- 官方机器人的 ``user_openid/group_openid/message_id`` 是字符串，**不能**被转成数字，
  否则会丢字符（openid 是大写十六进制，不是 QQ 号）。

约定：所有「从会话 id 里取出来的目标」在交给协议适配器前走 :func:`as_int_id`；
适配器自己负责最终序列化（OneBot 发数字，官方发字符串）。
"""

from __future__ import annotations

from typing import Any

#: 领域层 ID 的类型（数字或字符串）
Id = int | str


def as_int_id(value: Any) -> Id:
    """把 ID 归一成「能变数字就数字，否则原样字符串」。

    >>> as_int_id("123")
    123
    >>> as_int_id("E4F4AEA33253A2797FB897C50B81D7ED")
    'E4F4AEA33253A2797FB897C50B81D7ED'
    >>> as_int_id(None)
    ''
    """
    if value is None:
        return ""
    if isinstance(value, bool):  # bool 是 int 子类，不该被当 ID 用
        return int(value)
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    return text


def id_text(value: Any) -> str:
    """把 ID 渲染成字符串（日志 / 展示 / 查询键用）。"""
    if value is None:
        return ""
    return str(value)


def same_id(left: Any, right: Any) -> bool:
    """宽松比较两个 ID（``1`` 与 ``"1"`` 视为同一个）。"""
    return id_text(left) == id_text(right)
