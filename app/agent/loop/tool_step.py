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

## 边界

- **不负责取料**（组装 messages）、**不负责发送**（那是 `delivery/` 的事）、
  **不负责收尾记账**（调用方自己写历史/遥测）。
- 只认两种正常结局：模型不再要工具（`assistant`），或轮数用尽（`max_rounds`）。
  取消与中断由 P2 的 `AbortScope` 接管，本模块不提前引入。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.domain.splitter import split_sentences, strip_stream_artifacts

#: 一次步骤的结局
KIND_ASSISTANT = "assistant"      # 模型不再要工具，正常结束
KIND_MAX_ROUNDS = "max_rounds"    # 轮数用尽
KIND_ERROR = "error"              # 请求失败
KIND_EMPTY = "empty"              # 一轮请求都没有发生过（max_rounds <= 0）


@dataclass
class RoundSummary:
    """一轮工具执行完毕后交给调用方的事实（不含"要不要发"的判断）。"""

    index: int
    rounds_left: int
    round_text: str
    tool_results: list[dict] = field(default_factory=list)


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
    rounds: int = 0


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


async def run_stream_step(req: StepRequest) -> StepResult:
    """跑完一次流式步骤（含工具多轮），返回结构化结局。

    调用方只需：拿到 ``StepResult`` 后决定发什么、记什么账。
    """
    from app.agent.loop.tool_loop import normalize_and_execute_tool_calls
    from app.agent.loop.provider_view import _modalities_for_chain
    from app.agent.providers.invoker import resolve_invoker
    from app.agent.tools.round_images import append_round_images

    result = StepResult()
    if req.max_rounds <= 0:
        return result

    # provider 只负责"一次请求"：工具执行在这里，所以永远不要把执行器继续往下传，
    # 否则适配器会自己再跑一遍循环（P1 收口要消灭的正是这件事）。
    params = {k: v for k, v in req.params.items() if k != "tool_executor"}
    executor = req.params.get("tool_executor")
    invoker = resolve_invoker(req.runtime)
    modalities = req.modalities
    if modalities is None:
        modalities = _modalities_for_chain(req.chain)

    text_parts: list[str] = []
    for round_index in range(req.max_rounds):
        result.rounds = round_index + 1
        round_start = len(text_parts)      # 本轮新增文本从这一格开始
        buffer = ""
        tool_calls: dict[int, dict] = {}
        finish_reason = ""
        round_error = ""
        reasoning_chars = 0

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
                        if req.on_text is not None:
                            await req.on_text(clean)
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
            if req.on_text is not None:
                await req.on_text(tail)

        result.reasoning_chars += reasoning_chars
        result.finish_reason = finish_reason or result.finish_reason

        if round_error:
            result.error = round_error
            result.kind = KIND_ERROR
            break

        if not tool_calls:
            result.kind = KIND_ASSISTANT
            break

        calls = [tool_calls[idx] for idx in sorted(tool_calls)]
        round_text = "".join(text_parts[round_start:])
        normalized, tool_messages = await normalize_and_execute_tool_calls(
            calls, executor, result.tool_results
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
        append_round_images(req.messages, req.tool_ctx, modalities)

        if req.on_round_end is not None:
            await req.on_round_end(RoundSummary(
                index=round_index,
                rounds_left=req.max_rounds - (round_index + 1),
                round_text=round_text,
                tool_results=list(result.tool_results),
            ))
    else:
        result.kind = KIND_MAX_ROUNDS

    result.text = "".join(text_parts)
    return result
