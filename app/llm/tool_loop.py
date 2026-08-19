"""共享工具调用处理：标准化 + 执行 + 构造回传消息。

供流式（chat.stream_response）与非流式（providers.openai_compat.chat）两条工具循环
共同使用，消除“两套工具循环各实现一遍、修一处漏一处”的分歧。

统一约定（根治流式工具调用的两大问题）：
- **id 自洽**：协议只要求“本轮 assistant 消息里的 tool_calls id”与紧随的
  ``role=tool`` 消息的 ``tool_call_id`` 一致即可，id 不依赖上游返回的真实性。
  流式碎片常见的 ``id=null/缺失`` 在这里会被补成 ``call_{index}``，保证
  round-2 请求合法，不再出现“工具执行成功但回传 tool_call_id 为空被 API 拒绝”。
- **arguments 归一**：流式场景是增量拼接的 JSON 字符串，非流式场景可能是字符串或 dict，
  这里统一解析为 dict 后再交给工具执行器。
"""

from __future__ import annotations

import json
from typing import Any, Callable


async def normalize_and_execute_tool_calls(
    tool_calls: list[dict],
    tool_executor: Callable | None,
    tool_results: list[dict],
) -> tuple[list[dict], list[dict]]:
    """标准化并执行一批工具调用，返回用于回传协同一对消息。

    Args:
        tool_calls: 原始 tool_call 列表，每项含 ``id`` / ``function.name`` /
            ``function.arguments``（可为 None / 增量 JSON 字符串 / dict）。
        tool_executor: ``async (name, args) -> str`` 工具执行器；None 时返回占位结果。
        tool_results: 执行结果累积列表（就地 append，供调用方记录/兜底判断）。

    Returns:
        ``(normalized_tool_calls, tool_result_messages)``：
        - ``normalized_tool_calls``：标准化后的 tool_call（id 已自洽非空，
          arguments 已解析为 dict），用于构造 assistant 消息的 ``tool_calls``，
          保证与回传消息 id 一一对应；
        - ``tool_result_messages``：``role=tool`` 回传消息列表。
    """
    normalized: list[dict] = []
    result_messages: list[dict] = []

    for index, raw in enumerate(tool_calls):
        tc = dict(raw or {})
        fn = dict(tc.get("function") or {})
        name = fn.get("name") or ""
        raw_args = fn.get("arguments") or ""

        if isinstance(raw_args, str) and raw_args.strip():
            try:
                args = json.loads(raw_args)
                if not isinstance(args, dict):
                    args = {}
            except json.JSONDecodeError:
                args = {}
            # 上游本来就是 JSON 字符串时原样保留，符合 OpenAI 协议的
            # function.arguments 必须为 string 的约束。
            wire_args = raw_args
        elif isinstance(raw_args, dict):
            args = raw_args
            wire_args = json.dumps(args, ensure_ascii=False)
        else:
            args = {}
            wire_args = "{}"

        # 绝不让 tool_call_id 为空：缺 id（流式常见 id=null）时生成本轮自洽 id，
        # 否则该轮工具已执行却因回传 id 空被 API 拒绝，导致整轮失败。
        tc_id = str(tc.get("id") or "").strip()
        if not tc_id:
            tc_id = f"call_{index}"
        tc["id"] = tc_id
        fn["name"] = name
        # 线上 assistant 消息里 arguments 必须是 JSON 字符串（dict 会导致
        # round-2 请求 400 校验失败）；解析后的 dict 仅用于执行器与结果记录。
        fn["arguments"] = wire_args
        tc["function"] = fn

        try:
            exec_result = await tool_executor(name, args) if tool_executor else "工具执行器不可用"
        except Exception as e:  # noqa: BLE001 - 工具异常应以结果回传而非中断请求
            exec_result = f"error: 工具执行异常 {e}"
        if not isinstance(exec_result, str):
            exec_result = str(exec_result)

        tool_results.append({"name": name, "args": args, "result": exec_result})
        normalized.append(tc)
        result_messages.append({"role": "tool", "tool_call_id": tc_id, "content": exec_result})

    return normalized, result_messages
