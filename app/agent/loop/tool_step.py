"""一次「请求 → 工具批次 → 回填」的共享步骤运行器（P1 收口）。

## 为什么要有这个文件

工具循环此前有 **6 份**实现，形状一样、细节各不同：

| 位置 | 产出方式 | round 边界额外动作 |
|---|---|---|
| `chat.stream_response` | `yield` 句子/气泡（async generator） | 发言通道 flush + `SPEAK_REMINDER` |
| `turn._stream_once` | `await on_sentence(text)` | 无（只补图） |
| `plugins/llm_proactive/stream.py` | `await pool.put(Message)` | 无（只补图） |
| `providers/{openai_compat,anthropic,gemini}.chat` | 返回 `LLMResponse` | 无 |

于是"改一处漏一处"成了常态：`expand_image` 补图轮、补全登记、空回复重试
都曾在某一条路径上单独修过（见 `tests/architecture/test_turn_path_parity.py` 的事故记录）。

本模块把**循环控制**收成唯一一份，差异用两个注入点表达：

- ``on_text``：一个完整句子（已切句、已去流式痕迹）；
- ``on_round_end``：一轮工具执行完毕（拿得到轮次、剩余轮数、本轮文本、工具结果）。

两个入口共用同一套语义：`run_stream_step`（`invoker.stream`）与
`run_blocking_step`（`invoker.chat`，适配器只发一次请求）。

## 边界

- **不负责取料**（组装 messages）、**不负责发送**（那是 `delivery/` 的事）、
  **不负责收尾记账**（调用方自己写历史/遥测）。
- 只认三种结局：模型不再要工具（`assistant`）、轮数用尽（`max_rounds`）、
  请求失败（`error`）。非流式另有一种"请求成功但没有正文"（`empty`），
  空回复兜底与"思考吃满预算"的收敛重试都留给调用方（它们改的是 prompt/话术）。
  取消与中断由 P2 的 `AbortScope` 接管，本模块不提前引入。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.domain.splitter import split_sentences, strip_stream_artifacts

#: 一次步骤的结局
KIND_ASSISTANT = "assistant"      # 模型不再要工具，正常结束
KIND_MAX_ROUNDS = "max_rounds"    # 轮数用尽
KIND_ERROR = "error"              # 请求失败
KIND_EMPTY = "empty"              # 一轮请求都没有发生过（max_rounds <= 0）

#: 事件类型（`iter_stream_step` 的产出）
STEP_TEXT = "text"            # 一个完整句子
STEP_ROUND_END = "round_end"  # 一轮结束（带 RoundSummary）

#: 取消时给"模型要了但没派发"的工具补的结果（工具错误码之一，见计划 §1.2）
ABORTED_BEFORE_DISPATCH = "error: ABORTED_BEFORE_DISPATCH 本轮已被取消，该工具未执行"


class TurnAborted(Exception):
    """本轮被协作式取消（由 `turn_runner.AbortScope.throw_if_aborted` 抛出）。

    定义在**本模块**而不是 `turn_runner`：异常是在这里（边界处）抛出的，而
    `turn_runner` 已经依赖本模块，反向 import 会成环。`turn_runner` 重新导出它。

    刻意不是 `asyncio.CancelledError`：取消只发生在**边界**（请求前 / 工具派发前），
    已经发出去的消息与已经写进历史的前缀必须留下，所以不能用 task.cancel() 那种
    "任意位置被打断"的语义。
    """

    def __init__(self, cause: str = "") -> None:
        self.cause = cause or "turn aborted"
        super().__init__(self.cause)


@dataclass
class RoundSummary:
    """一轮请求结束后交给调用方的事实（不含"要不要发"的判断）。

    带齐调用方记账要用的数字：轮次 / 剩余轮数 / 本轮文本 / 工具结果 / 是否补图，
    以及这一轮的 finish_reason、思考字符、错误、本轮开始前的 usage 快照
    （请求行日志要据此算"这一轮花了多少"）。
    """

    index: int
    rounds_left: int
    round_text: str
    tool_results: list[dict] = field(default_factory=list)
    #: 本轮是否有 ``expand_image`` 取回的图片被递给模型（调用方据此留痕）
    images_appended: bool = False
    finish_reason: str = ""
    reasoning_chars: int = 0
    #: 本轮模型要求调用的工具数（0 = 收尾轮）
    tool_calls: int = 0
    error: str = ""
    #: 本轮请求开始前的 usage 快照（累加器是调用方传进 params 的 ``usage_sink``）
    usage_before: dict = field(default_factory=dict)


@dataclass
class StepRequest:
    """一次步骤的全部输入。

    ``messages`` 是**就地追加**的（与今天的六处循环一致）；P4 改成"日志派生 + 快照"。
    """

    runtime: Any
    chain: list[dict]
    messages: list[dict]
    params: dict
    max_rounds: int = 5
    tool_ctx: Any = None
    modalities: Any = None
    #: 一个完整句子回调（流式）；None = 只累积不产出
    on_text: Callable[[str], Awaitable[None]] | None = None
    #: 每轮工具执行完之后回调（发言通道 flush / 提醒注入的挂点）
    on_round_end: Callable[[RoundSummary], Awaitable[None]] | None = None
    #: 单句最大长度（切句用）
    max_msg_len: int = 200
    #: 协作式取消句柄（`turn_runner.AbortScope`）：每轮请求前与每次工具派发前检查
    abort: Any = None
    #: 会话事件日志（`session_log.SessionLog`）：记 tool/call 与 tool/result（Q3）
    tool_log: Any = None


@dataclass
class StepResult:
    """一次步骤的结局 + 全过程事实（调用方据此记账，不再自己数轮子）。"""

    kind: str = KIND_EMPTY
    text: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)
    finish_reason: str = ""
    error: str = ""
    reasoning_chars: int = 0
    #: 最后一轮的思考字符数（"思考吃满预算"的收敛判断只看它，不看累计值）
    last_round_reasoning: int = 0
    rounds: int = 0
    #: 本次步骤的 token 用量（非流式路径跨轮累加；流式由调用方的 ``usage_sink`` 记账）
    usage: dict = field(default_factory=dict)


def accumulate_tool_call(slot_map: dict, tc: dict) -> None:
    """把流式工具调用碎片按 index 累积进 ``slot_map``。

    此前 `chat.py` 与 `llm_proactive/stream.py` 各有一份**逐字相同**的实现；
    这里收成一份，并保留两处既有防御：碎片里的 ``id`` / ``name`` / ``arguments``
    可能是 ``null``（部分网关如此），直接拼接会抛 TypeError。
    """
    index = int((tc or {}).get("index", 0) or 0)
    slot = slot_map.setdefault(index, {
        "id": "",
        "type": "function",
        "function": {"name": "", "arguments": ""},
    })
    frag = tc.get("function") or {}
    slot["id"] += tc.get("id") or ""
    slot["function"]["name"] += frag.get("name") or ""
    slot["function"]["arguments"] += frag.get("arguments") or ""


async def iter_stream_step(req: StepRequest, result: StepResult) -> AsyncIterator[tuple[str, Any]]:
    """流式步骤的**事件流**：``("text", 句子)`` 与 ``("round_end", RoundSummary)``。

    为什么要有这一层：`chat.stream_response` 是 async generator（要 ``yield`` 句子和
    发言通道的消息），而回调式 ``on_text`` 里不能 yield。把产出变成**事件**，调用方
    就能在 ``async for`` 里直接 yield，不需要队列中转，也就不会把"句间打断"延后一整轮。

    ``result`` 由本函数就地填好（结局/文本/工具结果/思考计数）；调用方提前
    ``aclose()`` 时也能拿到已经发生的事实。
    """
    from app.agent.loop.provider_view import _modalities_for_chain
    from app.agent.providers.invoker import resolve_invoker

    if req.max_rounds <= 0:
        return

    # provider 只负责"一次请求"：工具执行在这里，所以永远不要把执行器继续往下传，
    # 否则适配器会自己再跑一遍循环（P1 收口要消灭的正是这件事）。
    params = {k: v for k, v in req.params.items() if k != "tool_executor"}
    executor = req.params.get("tool_executor")
    usage_sink = req.params.get("usage_sink")
    invoker = resolve_invoker(req.runtime)
    modalities = req.modalities
    if modalities is None:
        modalities = _modalities_for_chain(req.chain)

    text_parts: list[str] = []
    try:
        async for item in _drive_stream_rounds(
            req, result, text_parts, params=params, executor=executor,
            invoker=invoker, modalities=modalities, usage_sink=usage_sink,
        ):
            yield item
    finally:
        # 调用方提前 aclose（句间打断）时也要留下"已经产出到哪"的事实
        result.text = "".join(text_parts)


async def _drive_stream_rounds(
    req: StepRequest,
    result: StepResult,
    text_parts: list[str],
    *,
    params: dict,
    executor: Any,
    invoker: Any,
    modalities: Any,
    usage_sink: Any,
) -> AsyncIterator[tuple[str, Any]]:
    """`iter_stream_step` 的轮次主体（拆出来只为让 ``text_parts`` 有明确的收尾）。"""
    from app.agent.loop.tool_loop import normalize_and_execute_tool_calls
    from app.agent.session.session_log import (
        EVENT_TOOL_CALL,
        EVENT_TOOL_RESULT,
        safe_append,
    )
    from app.agent.tools.round_images import append_round_images

    def _on_tool_event(kind: str, payload: dict) -> int:
        """把一次工具调用/结果记进会话日志（Q3）；没有日志就返回 0。"""
        if req.tool_log is None:
            return 0
        return safe_append(
            req.tool_log, EVENT_TOOL_CALL if kind == "call" else EVENT_TOOL_RESULT, **payload
        )

    for round_index in range(req.max_rounds):
        # 取消只在**边界**生效：上一轮已经发出去的句子、已经写进历史的前缀都算数，
        # 所以检查点放在"要发下一个请求之前"，而不是流式产出的中途。
        if req.abort is not None:
            req.abort.throw_if_aborted()
        result.rounds = round_index + 1
        round_start = len(text_parts)      # 本轮新增文本从这一格开始
        buffer = ""
        tool_calls: dict[int, dict] = {}
        finish_reason = ""
        round_error = ""
        reasoning_chars = 0
        usage_before = dict(usage_sink) if isinstance(usage_sink, dict) else {}

        async for event in invoker.stream(
            runtime=req.runtime, chain=req.chain, messages=req.messages, params=params
        ):
            if event.type == "text":
                buffer += event.text or ""
                sentences, buffer = split_sentences(buffer, max_length=req.max_msg_len)
                for sentence in sentences:
                    clean = strip_stream_artifacts(sentence)
                    if clean:
                        text_parts.append(clean)
                        yield STEP_TEXT, clean
            elif event.type == "tool_call":
                accumulate_tool_call(tool_calls, event.tool_call or {})
            elif event.type == "reasoning":
                # 思考只计数、绝不产出：调用方用它与"真空回复"区分
                reasoning_chars += len(event.text or "")
            elif event.type == "error":
                round_error = event.text or "流式请求失败"
                break
            elif event.type == "done":
                finish_reason = event.finish_reason or finish_reason

        tail = strip_stream_artifacts(buffer.strip())
        if tail:
            text_parts.append(tail)
            yield STEP_TEXT, tail

        result.reasoning_chars += reasoning_chars
        result.last_round_reasoning = reasoning_chars
        result.finish_reason = finish_reason or result.finish_reason
        round_text = "".join(text_parts[round_start:])

        if round_error:
            result.error = round_error
            result.kind = KIND_ERROR
            # 失败轮也要交账（请求行日志里 error 字段就是靠它填的）
            yield STEP_ROUND_END, RoundSummary(
                index=round_index,
                rounds_left=req.max_rounds - (round_index + 1),
                round_text=round_text,
                tool_results=list(result.tool_results),
                finish_reason=finish_reason,
                reasoning_chars=reasoning_chars,
                error=round_error,
                usage_before=usage_before,
            )
            break

        if not tool_calls:
            result.kind = KIND_ASSISTANT
            yield STEP_ROUND_END, RoundSummary(
                index=round_index,
                rounds_left=req.max_rounds - (round_index + 1),
                round_text=round_text,
                tool_results=list(result.tool_results),
                finish_reason=finish_reason,
                reasoning_chars=reasoning_chars,
                usage_before=usage_before,
            )
            break

        calls = [tool_calls[idx] for idx in sorted(tool_calls)]
        # 派发前再查一次：模型刚给出的这批工具调用如果不再需要，就不该真的去执行
        # （执行一个已经没人要的写操作是不可撤销的）。补配对结果后再抛：assistant 里
        # 带了 tool_calls 就必须有 role=tool 回执，否则这条 messages 下次回放会被 400。
        if req.abort is not None:
            try:
                req.abort.throw_if_aborted()
            except TurnAborted:
                from app.agent.loop.tool_loop import aborted_tool_messages

                normalized, aborted_messages = aborted_tool_messages(calls, result.tool_results)
                result.tool_calls = normalized
                req.messages.append({
                    "role": "assistant",
                    "content": round_text or None,
                    "tool_calls": normalized,
                })
                req.messages.extend(aborted_messages)
                raise
        normalized, tool_messages = await normalize_and_execute_tool_calls(
            calls, executor, result.tool_results, on_tool_event=_on_tool_event
        )
        result.tool_calls = normalized
        req.messages.append({
            "role": "assistant",
            "content": round_text or None,
            "tool_calls": normalized,
        })
        req.messages.extend(tool_messages)

        # 工具轮里 expand_image 取回的图片必须**在本轮**递给模型：
        # 否则模型当轮只能凭工具回执里的文字描述瞎猜（历史事故）。
        images_appended = append_round_images(req.messages, req.tool_ctx, modalities)

        yield STEP_ROUND_END, RoundSummary(
            index=round_index,
            rounds_left=req.max_rounds - (round_index + 1),
            round_text=round_text,
            tool_results=list(result.tool_results),
            images_appended=images_appended,
            finish_reason=finish_reason,
            reasoning_chars=reasoning_chars,
            tool_calls=len(calls),
            usage_before=usage_before,
        )
    else:
        result.kind = KIND_MAX_ROUNDS


async def run_stream_step(req: StepRequest) -> StepResult:
    """跑完一次流式步骤（含工具多轮），返回结构化结局。

    回调式封装：``on_text`` / ``on_round_end`` 逐个消费 ``iter_stream_step`` 的事件。
    需要"边产边 yield"的调用方（`chat.stream_response`）直接用事件流本身。
    """
    result = StepResult()
    events = iter_stream_step(req, result)
    try:
        async for kind, payload in events:
            if kind == STEP_TEXT:
                if req.on_text is not None:
                    await req.on_text(payload)
            elif req.on_round_end is not None:
                await req.on_round_end(payload)
    finally:
        # 调用方可能在本步骤中途退出（提前 return / 异常）：确保事件生成器被关掉，
        # 否则它会在事件循环的异步生成器终结器里被"稍后"回收。
        await events.aclose()
    return result


async def run_blocking_step(req: StepRequest) -> StepResult:
    """跑完一次非流式步骤（含工具多轮），返回结构化结局。

    与 `run_stream_step` 是同一套循环语义，只把 `invoker.stream` 换成
    `invoker.chat`：**适配器只发一次请求**（``tool_executor`` 到这里为止），
    工具执行、回填、补图都在这里。

    思考吃满预算的收敛重试与空回复兜底话术**不在这里**：它们改的是 prompt
    （追加提醒）或对外话术，属于调用方的职责（与改动前同口径）。
    """
    from app.agent.loop.provider_view import _modalities_for_chain
    from app.agent.loop.tool_loop import normalize_and_execute_tool_calls
    from app.agent.loop.turn_metrics import _input_tokens_estimate
    from app.agent.providers.base import merge_usage
    from app.agent.providers.invoker import resolve_invoker
    from app.agent.session.session_log import (
        EVENT_TOOL_CALL,
        EVENT_TOOL_RESULT,
        safe_append,
    )
    from app.agent.tools.round_images import append_round_images

    def _log_tool_event(kind: str, payload: dict) -> int:
        """非流式路径同样记 tool/call 与 tool/result（Q3）。"""
        if req.tool_log is None:
            return 0
        return safe_append(
            req.tool_log, EVENT_TOOL_CALL if kind == "call" else EVENT_TOOL_RESULT, **payload
        )

    result = StepResult()
    if req.max_rounds <= 0:
        return result

    params = {k: v for k, v in req.params.items() if k != "tool_executor"}
    executor = req.params.get("tool_executor")
    invoker = resolve_invoker(req.runtime)
    modalities = req.modalities
    if modalities is None:
        modalities = _modalities_for_chain(req.chain)

    for round_index in range(req.max_rounds):
        if req.abort is not None:
            req.abort.throw_if_aborted()
        result.rounds = round_index + 1
        # 每轮都重算上下文规模：工具回填让 messages 变长，预算闸门必须看见真实值
        params["input_tokens_estimate"] = _input_tokens_estimate(req.messages)
        response = await invoker.chat(
            runtime=req.runtime, chain=req.chain, messages=req.messages, params=params
        )

        usage = getattr(response, "usage", None)
        if isinstance(usage, dict):
            merge_usage(result.usage, usage)
        text = str(getattr(response, "text", "") or "")
        reasoning = str(getattr(response, "reasoning", "") or "")
        result.reasoning_chars += len(reasoning)
        calls = list(getattr(response, "tool_calls", None) or [])

        # finish_reason 只有 raw 里有（OpenAI 形状）：结局判定要用它区分
        # "被 max_tokens 截断"（MAX_TOKENS）和"模型就是没话说"（SILENT）。
        raw = getattr(response, "raw", None)
        if isinstance(raw, dict):
            choices = raw.get("choices") or []
            if choices and isinstance(choices[0], dict):
                result.finish_reason = str(choices[0].get("finish_reason") or "")

        if not calls:
            result.text = text
            result.tool_calls = []
            result.kind = KIND_ASSISTANT if text.strip() else KIND_EMPTY
            break

        # 与流式路径同一口径：取消时补配对结果，再往上抛。
        if req.abort is not None:
            try:
                req.abort.throw_if_aborted()
            except TurnAborted:
                from app.agent.loop.tool_loop import aborted_tool_messages

                normalized, aborted_messages = aborted_tool_messages(calls, result.tool_results)
                result.tool_calls = normalized
                req.messages.append({
                    "role": "assistant",
                    "content": text or None,
                    "tool_calls": normalized,
                })
                req.messages.extend(aborted_messages)
                raise

        normalized, tool_messages = await normalize_and_execute_tool_calls(
            calls, executor, result.tool_results, on_tool_event=_log_tool_event
        )
        result.tool_calls = normalized
        req.messages.append({
            "role": "assistant",
            "content": text or None,
            "tool_calls": normalized,
        })
        req.messages.extend(tool_messages)

        images_appended = append_round_images(req.messages, req.tool_ctx, modalities)

        if req.on_round_end is not None:
            await req.on_round_end(RoundSummary(
                index=round_index,
                rounds_left=req.max_rounds - (round_index + 1),
                round_text=text,
                tool_results=list(result.tool_results),
                images_appended=images_appended,
            ))
    else:
        result.kind = KIND_MAX_ROUNDS

    return result
