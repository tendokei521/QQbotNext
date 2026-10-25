"""压缩时**绝不切开 tool_call / tool_result 配对**（P5 第 4 步）。

## 为什么

`split_history` 目前按下标切：`history[:-keep]` 与 `history[-keep:]`。一旦历史里出现
工具消息（P4 之后"是否让工具消息进历史"就摆在桌上了），切点可能正好落在
`assistant(tool_calls)` 与紧随的 `role:tool` 之间——于是：

- 保留段的第一条是"没有 tool_call 的 tool 结果"，摘要段最后一条是"没有结果的
  tool_calls"，上游直接 400（协议要求成对）。

今天不可达（工具消息还不进历史），但它是那条产品决策的**前置条件**，所以先做。

## 做法

切点校验：`tool_calls_before(cut) == tool_results_before(cut)` 才算合法；不满足就把
切点**往前挪**（旧段变小一点），直到配对闭合。`split_history` 的签名与返回形状不变。
"""

from __future__ import annotations


def _is_tool_call(message: dict) -> bool:
    return str(message.get("role") or "") == "assistant" and bool(message.get("tool_calls"))


def _is_tool_result(message: dict) -> bool:
    return str(message.get("role") or "") == "tool"


def pairing_is_closed(history: list[dict], cut: int) -> bool:
    """切在 ``cut`` 处（``history[:cut]`` 归旧段）时，被切断的配对是否闭合。

    判据：旧段里"带 tool_calls 的 assistant"数量 == "role:tool" 数量。
    只数形状而不认 id，是因为模型给的 id 可能留空（由共享 helper 兜底成 ``call_i``），
    而"配对是否成对"与 id 的具体取值无关。
    """
    if cut <= 0 or cut >= len(history):
        return True
    old = history[:cut]
    calls = sum(1 for m in old if isinstance(m, dict) and _is_tool_call(m))
    results = sum(1 for m in old if isinstance(m, dict) and _is_tool_result(m))
    return calls == results


def legal_cut(history: list[dict], keep_count: int) -> int:
    """在"保留最近 ``keep_count`` 条"附近找一个**配对闭合**的切点。

    优先用原切点；不合法就往前挪（保留段变长，旧段变短），最坏挪到 0（不压缩），
    宁可少压一轮也不发出非法请求。
    """
    cut = len(history) - keep_count
    while 0 < cut < len(history) and not pairing_is_closed(history, cut):
        cut -= 1
    return max(0, min(cut, len(history)))
