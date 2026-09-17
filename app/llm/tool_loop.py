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

执行策略（两处生产化修正）：
- **并行执行**：一轮内的多个 tool_call 此前是串行 ``await``，而单个工具有 20s 超时
  （``tool.TOOL_TIMEOUT``）。模型一次展开多个 id（本项目的“补全消息环境”会大量出现）
  时，3 个工具最坏 60s，直接撞上 Provider 的请求超时（默认 30s）——表现为“工具都执行
  完了，整轮请求却已超时”。这里改为 ``asyncio.gather`` 并发执行，墙钟时间从 Σ 降为 max。
- **参数解析失败不再静默降级**：以前 ``arguments`` 解析失败会退化成 ``args={}`` 照常执行，
  工具收到空参后返回 error/误导结果回传给模型，模型只会得出“这工具不好用”的结论而放弃
  调用（正是“主动性”流失的主要来源之一）。现在直接回传可纠正的错误文本，不执行工具。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable


def _parse_arguments(raw_args: Any) -> tuple[dict, str, str]:
    """解析一份工具参数，返回 ``(args, wire_args, parse_error)``。

    ``wire_args`` 是回填进 assistant 消息的 ``function.arguments``（协议要求必须是 JSON
    字符串）；``parse_error`` 非空表示参数不可用，调用方应回传错误而不是执行工具。
    """
    if isinstance(raw_args, str) and raw_args.strip():
        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError as e:
            return {}, "{}", f"不是合法 JSON（{e.msg}）"
        if not isinstance(args, dict):
            return {}, "{}", f"必须是 JSON 对象，收到 {type(args).__name__}"
        # 上游本来就是 JSON 字符串时原样保留，符合 OpenAI 协议的
        # function.arguments 必须为 string 的约束。
        return args, raw_args, ""
    if isinstance(raw_args, dict):
        return raw_args, json.dumps(raw_args, ensure_ascii=False), ""
    return {}, "{}", ""


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
    prepared: list[dict] = []
    for index, raw in enumerate(tool_calls):
        tc = dict(raw or {})
        fn = dict(tc.get("function") or {})
        name = fn.get("name") or ""
        args, wire_args, parse_error = _parse_arguments(fn.get("arguments") or "")

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

        prepared.append({
            "tc": tc,
            "id": tc_id,
            "name": name,
            "args": args,
            "parse_error": parse_error,
        })

    async def _execute(item: dict) -> str:
        if item["parse_error"]:
            return (
                f"error: 工具 {item['name']} 调用参数不合法：{item['parse_error']}。"
                "请按该工具的 parameters schema 重新以合法 JSON 调用。"
            )
        try:
            result = await tool_executor(item["name"], item["args"]) if tool_executor else "工具执行器不可用"
        except Exception as e:  # noqa: BLE001 - 工具异常应以结果回传而非中断请求
            return f"error: 工具执行异常 {e}"
        return result if isinstance(result, str) else str(result)

    # 并行执行：gather 保序，回填顺序与 tool_call 一一对应（见模块 docstring）。
    exec_results = await asyncio.gather(*[_execute(item) for item in prepared])

    normalized: list[dict] = []
    result_messages: list[dict] = []
    for item, exec_result in zip(prepared, exec_results):
        tool_results.append({"name": item["name"], "args": item["args"], "result": exec_result})
        normalized.append(item["tc"])
        result_messages.append({
            "role": "tool",
            "tool_call_id": item["id"],
            "content": exec_result,
        })

    return normalized, result_messages
