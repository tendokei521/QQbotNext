"""发言方式（send mode）：一轮回复**分几条、什么时候发**。

## 两个正交维度

| 维度 | 配置 | 回答的问题 |
|---|---|---|
| **输出方式**（传输） | `stream_output` | 请求怎么要：流式边收边用 / 普通请求一次拿全 |
| **发言方式**（投递） | `reply_send_mode` | 拿到的正文怎么发：一条整段 / 收到就发 / 拆开按节奏发 |

历史实现把「切句发送」焊死在流式里（句子在 `loop/tool_step.py` 边收边切，
非流式 `BlockingOutput` 只会整段发一条），于是「非流式也要按换行拆」只能改源码。
现在投递侧自己决定发送形态，两种传输都能选三种发言方式：

| `reply_send_mode` | 流式请求 | 普通请求 |
|---|---|---|
| `whole` 整段一起发 | 边收只攒着，结束发一条 | 结束发一条（默认口径） |
| `realtime` 收到就发 | 每切出一句立刻发 | 没有「边收」，退化为整段发 |
| `split` 拆分节奏 | 每切出一句发，按节奏等待 | 整轮拿全后按换行/句末标点拆开发 |
| `auto` 自动（默认） | = `realtime` | = `whole` |

`auto` 只为升级平滑：老用户什么都没配时行为与改造前逐字一致。老配置里
`stream_send_pool_enabled=True`（旧「消息池」总闸）在 `auto` 下映射为 `split`，
否则升级后发送节奏会静默消失。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.domain.message import Message, MessageSegment
from app.domain.splitter import split_for_delivery

#: 跟随输出方式（升级平滑用）
SEND_MODE_AUTO = "auto"
#: 一轮结束发一条
SEND_MODE_WHOLE = "whole"
#: 拿到一段就发（只对流式传输有实际意义）
SEND_MODE_REALTIME = "realtime"
#: 按换行/句末标点拆分，逐条按节奏发送（两种传输都支持）
SEND_MODE_SPLIT = "split"

SEND_MODES = frozenset({SEND_MODE_AUTO, SEND_MODE_WHOLE, SEND_MODE_REALTIME, SEND_MODE_SPLIT})

#: 旧「流式消息池」总闸：已从界面退役，只作为 `auto` 的迁移信号读取。
LEGACY_POOL_KEY = "stream_send_pool_enabled"

#: 未配置时的默认值（= 改造前行为）
DEFAULT_SEND_MODE = SEND_MODE_AUTO


def _get(config: Any, key: str, default: Any) -> Any:
    getter = getattr(config, "get", None)
    if not callable(getter):
        return default
    try:
        return getter(key, default)
    except Exception:  # noqa: BLE001 —— 配置读坏了按默认（保守、不中断发送）
        return default


def send_mode_of(config: Any) -> str:
    """原始配置值（非法值按 ``auto``，与配置自愈口径一致）。"""
    value = str(_get(config, "reply_send_mode", DEFAULT_SEND_MODE) or DEFAULT_SEND_MODE)
    return value if value in SEND_MODES else SEND_MODE_AUTO


def resolve_send_mode(config: Any, *, transport_stream: bool) -> str:
    """把「输出方式 × 发言方式」解析成这一轮实际怎么发。

    - 非流式传输没有「收到就发」可谈，``realtime`` 退化为 ``whole``；
    - ``auto`` 跟随传输方式，并兼容旧「消息池」开关（开启 = ``split``）。
    """
    mode = send_mode_of(config)
    if mode == SEND_MODE_AUTO:
        if bool(_get(config, LEGACY_POOL_KEY, False)):
            return SEND_MODE_SPLIT
        return SEND_MODE_REALTIME if transport_stream else SEND_MODE_WHOLE
    if mode == SEND_MODE_REALTIME and not transport_stream:
        return SEND_MODE_WHOLE
    return mode


def apply_affix(config: Any, msg: Message) -> Message:
    """给一条待发消息套上发送前缀/后缀（拆分节奏模式下逐条生效）。

    引用/@ 等非文本段保留在最前面：只替换文本段，不能把段结构拍平成纯文本。
    """
    prefix = str(_get(config, "stream_send_prefix", "") or "")
    suffix = str(_get(config, "stream_send_suffix", "") or "")
    if not prefix and not suffix:
        return msg
    if not isinstance(msg, Message):
        return Message.from_text(prefix + str(msg) + suffix)
    leading = [seg for seg in msg.segments if seg.type != "text"]
    leading.append(MessageSegment.text(prefix + msg.text + suffix))
    return Message(leading)


def split_messages(
    messages: Iterable[Any],
    *,
    max_length: int,
    include_affix: Any = None,
) -> list[Message]:
    """把整段待发消息拆成逐条发送的片段（顺序即发送顺序）。

    - 只拆文本段：``[reply]`` / ``[@QQ]`` 这些段挂在第一条上；
    - 没有文本的消息（纯图片/纯段结构）原样保留；
    - 原文本来就只有一片时不重新组装，避免丢掉段结构；
    - ``include_affix`` 传配置对象时，给每一片套上发送前缀/后缀。
    """
    out: list[Message] = []
    for raw in messages:
        msg = raw if isinstance(raw, Message) else Message.from_text(str(raw or ""))
        text = msg.text
        pieces = split_for_delivery(text, max_length=max_length) if text else []
        if not pieces:
            out.append(msg)
            continue
        if len(pieces) == 1:
            out.append(msg)
            continue
        leading = [seg for seg in msg.segments if seg.type != "text"]
        for index, piece in enumerate(pieces):
            segments: list[MessageSegment] = []
            if index == 0:
                segments.extend(leading)
            segments.append(MessageSegment.text(piece))
            out.append(Message(segments))
    if include_affix is not None:
        out = [apply_affix(include_affix, msg) for msg in out]
    return out
