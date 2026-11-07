"""把模型写在正文里的工具调用还原成真正的工具调用（发言通道的兜底）。

## 为什么需要

参与度模式（``reply_mode=participation``）的协议是「正文=思考，``send_message``=发言」，
而 assistant **历史**按同一协议渲染成 ``[tool_call:send_message]{"messages":"..."}``
（见 `app/agent/message_assembly/history.py::SPEECH_TOOL_CALL_MARKER`）。

线上仍然会出现模型**照抄历史写法**、把这一整行当普通正文输出、却始终不调用工具的
情况。此时正文只是思考、不会发出去，于是这一轮变成静默——用户看到的是"它想完了却
没说话"。这里把正文里符合该写法的片段解析出来，交给工具循环按**真正的工具调用**执行，
发送、历史、回执、后续轮次全部与模型自己调用工具时完全一致。

## 边界

- 只做**解析**：不执行、不发送、不改历史；
- 只还原**发言通道的两个工具**（``send_message`` / ``finish``）：正文→动作的兜底只服务
  于"该发言却写成了文字"，不能把任意一段文字变成任意工具的副作用；
- 容错优先：标记后面不是 JSON 对象、JSON 坏了、名字不认识——都当普通正文跳过，
  绝不让解析异常中断本轮。
"""

from __future__ import annotations

import json
import re

#: 正文里可能出现的最短写法：``[tool_call:send_message]{...}``
_MARKER_RE = re.compile(r"\[tool_call\s*:\s*(?P<name>[A-Za-z_][A-Za-z0-9_.\-]*)\s*\]")

#: JSON 解码器复用（``raw_decode`` 允许 JSON 后面继续跟其它正文）
_DECODER = json.JSONDecoder()

#: 允许从正文还原的工具：只限发言通道自己声明的两个
SPEECH_TOOL_NAMES: frozenset[str] = frozenset({"send_message", "finish"})


def extract_text_tool_calls(
    text: str,
    *,
    id_prefix: str = "text_call",
    allowed: frozenset[str] | set[str] | None = SPEECH_TOOL_NAMES,
) -> list[dict]:
    """从一段正文里解析出工具调用（OpenAI 形状，可直接并入 ``tool_calls``）。

    Args:
        text: 模型这一轮写出的正文。
        id_prefix: 生成 ``function call id`` 的前缀（同一轮多次调用从 1 递增）。
            跨轮重复的 id 可能让部分 Provider 拒绝下次请求，所以调用方应带上轮次。
        allowed: 允许还原的工具名；``None`` = 不限制（测试/特殊场景用）。

    Returns:
        ``[{"id", "type", "function": {"name", "arguments"}}]``（``arguments`` 是
        JSON 字符串，与真实工具调用同形）；解析不出任何一条时返回空列表。
    """
    raw = str(text or "")
    if not raw:
        return []

    calls: list[dict] = []
    for match in _MARKER_RE.finditer(raw):
        name = match.group("name")
        if allowed is not None and name not in allowed:
            continue
        rest = raw[match.end():]
        start = len(rest) - len(rest.lstrip())
        if start >= len(rest) or rest[start] != "{":
            continue
        try:
            arguments, _ = _DECODER.raw_decode(rest[start:])
        except ValueError:
            # 不是合法 JSON：说明是普通正文里恰好出现了这个前缀，跳过即可。
            continue
        if not isinstance(arguments, dict):
            continue
        calls.append({
            "id": f"{id_prefix}_{len(calls) + 1}",
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(arguments, ensure_ascii=False),
            },
        })
    return calls


__all__ = ["SPEECH_TOOL_NAMES", "extract_text_tool_calls"]
