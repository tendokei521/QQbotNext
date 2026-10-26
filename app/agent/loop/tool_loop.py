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

结果侧约定（回应要求）：
- 工具结果之后的那一轮生成，最后一条消息是 ``role: tool``——**没有新的用户发言**，
  模型的任务天然变成"处理/汇报这份结果"，于是输出长串总结（真实日志里 8 条、约 300 字）。
  人设与协议块都在上下文最前面，离生成点太远，压不住它。
- 因此这里提供「回应要求」文本，由工具执行器拼在**结果末尾**（位置最贴近生成），
  而不是新增一条 system 消息——Anthropic/Gemini 的适配器会把中途的 system 上提到最前面
  （``anthropic._normalize_messages`` / ``gemini._normalize_messages``），加了也会静默失效。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from app.agent.loop.middleware import (
    POINT_TOOL_CALL_BEFORE,
    POINT_TOOL_RESULT_AFTER,
    has_middleware,
    run_point,
)
from app.agent.loop.tool_step import ABORTED_BEFORE_DISPATCH

# 默认「回应要求」：只讲"用什么口吻回应"，不涉及具体内容
DEFAULT_REPLY_DIRECTIVE = (
    "（上面这些是给你自己看的资料，不是要你转述的稿子：直接用你自己的口吻回应用户，"
    "不要复述、不要列条目、不要以“根据记录/已展开”开头；一两句就够。"
    "若还需要更多信息，可以继续调用工具。）"
)

ERROR_RESULT_PREFIX = "error:"


def reply_directive(config: Any) -> str:
    """读取「回应要求」文本；未启用或读取失败时返回空串。"""
    if config is None:
        return ""
    try:
        if not bool(config.get("tool_result_directive_enable", True)):
            return ""
        text = config.get("tool_result_directive")
    except Exception:
        # 配置对象异常不应影响工具结果回传：按"不追加"处理（保守）
        return ""
    if text in (None, ""):
        return DEFAULT_REPLY_DIRECTIVE
    return str(text).strip()


def append_reply_directive(result: str, directive: str) -> str:
    """把「回应要求」拼到工具结果末尾。

    - 失败结果（``error:`` 前缀）不追加：此时模型需要的是纠错，不是语气约束；
    - 空结果不追加（保持"无内容"的语义）。
    """
    if not directive or not isinstance(result, str) or not result.strip():
        return result
    if result.lstrip().startswith(ERROR_RESULT_PREFIX):
        return result
    return f"{result}\n\n{directive}"


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


def _apply_tool_call_payload(item: dict, decided: dict) -> None:
    """把 ``tool_call.before`` 的改写结果写回执行项与 assistant 消息。

    - ``name`` / ``args`` / ``call_id`` 逐个生效，未给出的键保持原值；
    - 改了参数时**同步重写** ``function.arguments``（线上 assistant 消息里它必须是
      JSON 字符串），否则模型看到的历史与实际执行不一致；
    - 中间件显式给出合法 ``args`` 时清掉解析错误（它替模型修好了参数）。
    """
    name = str(decided.get("name") or "").strip()
    if name and name != item["name"]:
        item["name"] = name
        item["tc"]["function"]["name"] = name
    call_id = str(decided.get("call_id") or "").strip()
    if call_id and call_id != item["id"]:
        item["id"] = call_id
        item["tc"]["id"] = call_id
    args = decided.get("args")
    if isinstance(args, dict):
        item["args"] = args
        item["parse_error"] = ""
        item["tc"]["function"]["arguments"] = json.dumps(args, ensure_ascii=False)


async def normalize_and_execute_tool_calls(
    tool_calls: list[dict],
    tool_executor: Callable | None,
    tool_results: list[dict],
    *,
    on_tool_event: Callable[[str, dict], int] | None = None,
    runtime: Any = None,
    ctx: Any = None,
    structured_results: list | None = None,
) -> tuple[list[dict], list[dict]]:
    """标准化并执行一批工具调用，返回用于回传协同一对消息。

    Args:
        tool_calls: 原始 tool_call 列表，每项含 ``id`` / ``function.name`` /
            ``function.arguments``（可为 None / 增量 JSON 字符串 / dict）。
        tool_executor: ``async (name, args) -> str`` 工具执行器；None 时返回占位结果。
        tool_results: 执行结果累积列表（就地 append，供调用方记录/兜底判断）。
        on_tool_event: 会话事件日志回调（Q3）；返回事件序号。
        runtime: AgentRuntime。给出时启用中间件（``tool_call.before`` /
            ``tool_result.after``，见 ``app.agent.loop.middleware``）；None 时零开销。
        ctx: 透传给中间件的上下文（通常是 ``ToolContext``）。
        structured_results: 可选出参。给了就按**模型给出的顺序** append 每个调用的
            `ToolResult`（执行器支持 ``sink`` 时才有值，否则是 None）。Q6 的
            ``additional_contexts`` / ``concludes_turn`` 只能从这里读到——回传的
            字符串必须原样给 provider，不能把这些语义混进正文。

    Returns:
        ``(normalized_tool_calls, tool_result_messages)``：
        - ``normalized_tool_calls``：标准化后的 tool_call（id 已自洽非空，
          arguments 已解析为 dict），用于构造 assistant 消息的 ``tool_calls``，
          保证与回传消息 id 一一对应；
        - ``tool_result_messages``：``role=tool`` 回传消息列表。
    """
    from app.agent.providers import _accepts_kwarg
    from app.agent.tools.spec import is_error_result

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

    # Q7：tool_call.before —— **派发前**的最后一道改写/拦截。
    # 放在这里而不是执行器内部：改后的 name 还要参与并发分类（`execute_tool_batch`），
    # 改后的 args 要跟着进 assistant 消息的 arguments，两者必须同源。
    if has_middleware(runtime, POINT_TOOL_CALL_BEFORE):
        for item in prepared:
            decided = await run_point(
                runtime,
                POINT_TOOL_CALL_BEFORE,
                {"name": item["name"], "args": item["args"], "call_id": item["id"]},
                ctx=ctx,
            )
            if decided is None:
                # 被拦截：不派发，但仍要回一条 error 结果（assistant 的 tool_calls 必须配对）
                item["blocked_reason"] = f"error: 工具 {item['name']} 调用被中间件拦截"
            else:
                _apply_tool_call_payload(item, decided)

    # P4/Q3：把"模型要调的这批工具"记进会话日志（给回放/审计一个锚点）
    for item in prepared:
        if on_tool_event is not None:
            seq = on_tool_event("call", {
                "call_id": item["id"],
                "name": item["name"],
                "args_raw": str((item["tc"].get("function") or {}).get("arguments") or ""),
            })
            if seq:
                item["call_seq"] = int(seq)

    # 结构化结果旁路：make_executor 支持 ``sink=`` 时能拿到 ToolResult（含错误码/meta）。
    # 第三方执行器没有这个参数就退回"从文本推断"，行为不变。
    _accepts_sink = _accepts_kwarg(tool_executor, "sink")

    async def _execute(item: dict) -> str:
        if item.get("blocked_reason"):
            # 中间件在派发前拦下了这次调用：不执行，但结果照常回传（配对不破）
            return item["blocked_reason"]
        if item["parse_error"]:
            return (
                f"error: 工具 {item['name']} 调用参数不合法：{item['parse_error']}。"
                "请按该工具的 parameters schema 重新以合法 JSON 调用。"
            )
        try:
            if tool_executor is None:
                return "工具执行器不可用"
            if _accepts_sink:
                sink: dict = {}
                result = await tool_executor(item["name"], item["args"], sink=sink)
                item["structured"] = sink.get("result")
            else:
                result = await tool_executor(item["name"], item["args"])
        except Exception as e:  # noqa: BLE001 - 工具异常应以结果回传而非中断请求
            return f"error: 工具执行异常 {e}"
        return result if isinstance(result, str) else str(result)

    # 执行：按并发分类走"有界滚动池 + 独占屏障"，结果**按模型给出顺序**提交。
    exec_results = await execute_tool_batch(
        prepared,
        _execute,
        specs_by_name=getattr(tool_executor, "specs_by_name", None),
        parallel_max=int(getattr(tool_executor, "parallel_max", 1) or 1),
    )

    normalized: list[dict] = []
    result_messages: list[dict] = []
    _mw_result = has_middleware(runtime, POINT_TOOL_RESULT_AFTER)
    for item, exec_result in zip(prepared, exec_results):
        structured = item.get("structured")
        is_error = bool(getattr(structured, "is_error", False)) or is_error_result(exec_result)
        code = str(getattr(structured, "code", "") or "")
        # Q7：tool_result.after —— 结果回来之后、写进消息之前。
        # 改后的 result_text 同时用于 tool 消息与 tool_results，is_error/code 一并带进
        # 会话日志事件，保证"模型看到的"和"日志记的"是同一份。
        if _mw_result:
            decided = await run_point(
                runtime,
                POINT_TOOL_RESULT_AFTER,
                {
                    "name": item["name"],
                    "call_id": item["id"],
                    "result_text": exec_result,
                    "is_error": is_error,
                    "code": code,
                },
                ctx=ctx,
            )
            if decided is None:
                exec_result = f"error: 工具 {item['name']} 结果被中间件拦截"
                is_error = True
            else:
                text = decided.get("result_text")
                if isinstance(text, str):
                    exec_result = text
                if "is_error" in decided:
                    is_error = bool(decided.get("is_error"))
                if "code" in decided:
                    code = str(decided.get("code") or "")
        if on_tool_event is not None:
            meta = dict(getattr(structured, "meta", {}) or {})
            on_tool_event("result", {
                "call_id": item["id"],
                "name": item["name"],
                "is_error": is_error,
                "code": code,
                "chars": len(exec_result),
                "spilled_to": str(meta.get("spill") or ""),
                "source_event_seqs": [item["call_seq"]] if item.get("call_seq") else [],
            })
        tool_results.append({"name": item["name"], "args": item["args"], "result": exec_result})
        normalized.append(item["tc"])
        if structured_results is not None:
            # 与 normalized / result_messages 同序同长（拿不到结构的项就是 None），
            # 循环体据此下标回查"是哪个工具要求的收尾"。
            structured_results.append(item.get("structured"))
        result_messages.append({
            "role": "tool",
            "tool_call_id": item["id"],
            "content": exec_result,
        })

    return normalized, result_messages


async def execute_tool_batch(
    prepared: list[dict],
    execute,
    *,
    specs_by_name: dict | None = None,
    parallel_max: int = 1,
) -> list[str]:
    """按并发分类执行一批工具，返回**与 ``prepared`` 等长且同序**的结果。

    规则（对应计划 P3 的目标不变量）：

    1. 同一时刻在执行的工具数 ≤ ``parallel_max``（``1`` = 全串行）；
    2. 独占工具是**屏障**：它开始前先等在跑的并行批次全部结束，它结束后才继续；
    3. 回填顺序永远等于模型顺序 —— 结果按下标写回，与完成先后无关。

    分类来源是 `ToolSpec.runs_parallel(args)`（fail-closed）。拿不到 spec
    （自定义执行器）时一律按独占处理：串行只是慢，并发写错不可逆。
    """
    total = len(prepared)
    results: list[str | None] = [None] * total
    if total == 0:
        return []

    limit = max(1, int(parallel_max or 1))

    def _parallel(item: dict) -> bool:
        spec = (specs_by_name or {}).get(item["name"])
        if spec is None:
            return False
        try:
            return bool(spec.runs_parallel(item.get("args") or {}))
        except Exception:  # noqa: BLE001 —— 判定失败按独占（保守）
            return False

    pending: dict[asyncio.Task, int] = {}

    async def _drain_some() -> None:
        """等**任意一个**在跑的工具结束，把结果按下标写回。"""
        done, _ = await asyncio.wait(set(pending), return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            index = pending.pop(task)
            try:
                results[index] = task.result()
            except Exception as e:  # noqa: BLE001 —— 工具异常以结果回传，不中断整批
                results[index] = f"error: 工具执行异常 {e}"

    async def _drain_all() -> None:
        while pending:
            await _drain_some()

    try:
        for index, item in enumerate(prepared):
            if _parallel(item):
                if len(pending) >= limit:
                    await _drain_some()
                pending[asyncio.create_task(execute(item))] = index
                continue
            # 独占：先清空在跑的并行批次（屏障），再单独执行
            await _drain_all()
            task = asyncio.create_task(execute(item))
            pending[task] = index
            await _drain_all()
    finally:
        await _drain_all()

    return [r if r is not None else "" for r in results]

def aborted_tool_messages(
    tool_calls: list[dict],
    tool_results: list[dict] | None = None,
) -> tuple[list[dict], list[dict]]:
    """取消时给"模型要了但**没派发**"的工具补配对结果（不执行）。

    为什么必须补：assistant 消息里带了 ``tool_calls``，若没有紧随的 ``role=tool``
    回执，下次回放这条 messages 会被上游 400 拒绝（tool_calls 必须成对）。取消是
    正常结局，不能让一次取消把会话历史弄成不可回放的状态。

    与 `normalize_and_execute_tool_calls` 用同一套 id 自洽规则，但**不执行**——
    取消的语义就是"这些工具一个都没跑"。
    """
    normalized: list[dict] = []
    result_messages: list[dict] = []
    for index, raw in enumerate(tool_calls):
        tc = dict(raw or {})
        fn = dict(tc.get("function") or {})
        tc_id = str(tc.get("id") or "").strip() or f"call_{index}"
        tc["id"] = tc_id
        wire_args = fn.get("arguments")
        if not isinstance(wire_args, str):
            wire_args = json.dumps(wire_args or {}, ensure_ascii=False)
        fn["arguments"] = wire_args
        tc["function"] = fn
        normalized.append(tc)
        result_messages.append({
            "role": "tool",
            "tool_call_id": tc_id,
            "content": ABORTED_BEFORE_DISPATCH,
        })
        if tool_results is not None:
            tool_results.append({
                "name": fn.get("name") or "",
                "args": {},
                "result": ABORTED_BEFORE_DISPATCH,
            })
    return normalized, result_messages
