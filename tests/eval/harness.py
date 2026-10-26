"""X1 黑盒评测的最小驱动骨架（可复用）。

## 为什么是「真实循环 + 假 HTTP 底座」

评测要能回答"prompt / 工具循环 / 压缩一改是不是变差了"。如果把循环也换成假实现，
测到的只是假实现自己的行为。所以这里沿用
`tests/agent/loop/test_tool_loop_contract.py` 的做法：**继承真实的 `OpenAICompatProvider`，
只替换 `_request` / `_stream_once` 两个 HTTP 底座**，被测量的是真实工具循环。

驱动方式与 `tests/agent/loop/test_turn_reason_matrix.py` 一致：patch 掉
`prepare_prompt`（取料）与 `assembly.build_for`（组装），然后跑真实的 `run_turn`。
因此本骨架**不覆盖**取料链路（真实历史裁剪 / 记忆 / 知识库 / 工具清单），覆盖的是
"拿到 messages 之后"的一切：请求次数、工具执行顺序、结局判定、spill、遥测。

## 脚本格式（一条 = 一次模型响应）

`provider_script` 的每个元素可以是：

- 字符串 → 一段正文（最省事的写法）；
- dict → `{text|reasoning|tool_call|tool_calls|error|raise|finish_reason}`；
- 已构造好的 `ResponsePlan`；
- 直接给 `list[StreamEvent]`（流式）或 OpenAI 响应 dict（非流式），
  与参考契约测试的写法完全兼容。

## 用法

```python
runtime = make_runtime({"max_tool_rounds": 3})
result, requests = await run_scripted_turn(
    runtime,
    user_text="查一下",
    stream_script=[{"tool_call": {"name": "probe_a"}}, "好了。"],
)
assert len(requests) == 2
```

`requests_seen` 里每条是 `SeenRequest`：`.messages`（本次请求实际收到的 messages 快照）、
`.stream`、`.payload`。评测断言只读这三个字段，不碰 provider 内部。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent.providers import PROVIDERS
from app.agent.providers.base import StreamEvent
from app.agent.providers.openai_compat import OpenAICompatProvider
from app.agent.tools.spec import ToolSpec

#: 评测场景的默认配置：与 `tests/agent/loop` 的测试替身同口径，只保留跑通所需的最小集。
#: 重试类配置一律 0，让"请求次数"由脚本决定，不被重试噪声干扰。
DEFAULT_CONFIG: dict[str, Any] = {
    "history_rounds": 50,
    "max_message_length": 200,
    "max_tool_rounds": 5,
    "empty_reply_retries": 0,
    "reasoning_overflow_retries": 0,
    "stream_output": True,
    "tool_result_directive_enable": False,
    "tool_spill_enable": True,
    "tool_spill_max_bytes": 50000,
}

#: 探针工具的默认返回（场景里可用 `arguments.result` 覆盖）。
DEFAULT_TOOL_RESULTS: dict[str, str] = {
    "probe_a": "A 的结果",
    "probe_b": "B 的结果",
    "probe_c": "C 的结果",
    "probe_fail": "error: 上游 500（工具失败也应当回填给模型）",
    "probe_long": "长结果",
}

#: 探针工具名（场景脚本里可随意组合，顺序即执行顺序）。
PROBE_TOOLS: tuple[str, ...] = tuple(DEFAULT_TOOL_RESULTS)


class EvalHarnessError(RuntimeError):
    """评测脚本与真实请求对不上（这是**评测自身**的问题，不是被测代码的问题）。"""


@dataclass
class ResponsePlan:
    """一次脚本化响应的归一化表示（流式 / 非流式共用）。"""

    text: str = ""
    reasoning: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str = ""
    error: str = ""
    raise_error: str = ""
    raw_events: list[StreamEvent] | None = None
    raw_body: dict | None = None

    def stream_events(self) -> list[StreamEvent]:
        """转换成一条流式响应的事件序列。"""
        if self.raw_events is not None:
            return list(self.raw_events)
        if self.error:
            return [StreamEvent(type="error", text=self.error)]
        events: list[StreamEvent] = []
        if self.reasoning:
            events.append(StreamEvent(type="reasoning", text=self.reasoning))
        if self.text:
            events.append(StreamEvent(type="text", text=self.text))
        for call in self.tool_calls:
            events.append(StreamEvent(type="tool_call", tool_call=dict(call)))
        events.append(StreamEvent(type="done", finish_reason=self._finish()))
        return events

    def chat_body(self) -> dict:
        """转换成一次非流式响应的 OpenAI 响应体。"""
        if self.raw_body is not None:
            return dict(self.raw_body)
        if self.raise_error:
            raise RuntimeError(self.raise_error)
        message: dict[str, Any] = {"role": "assistant", "content": self.text or None}
        if self.reasoning:
            message["reasoning_content"] = self.reasoning
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": dict(call["function"]),
                }
                for call in self.tool_calls
            ]
        return {
            "choices": [{"finish_reason": self._finish(), "message": message}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 8},
        }

    def _finish(self) -> str:
        if self.finish_reason:
            return self.finish_reason
        return "tool_calls" if self.tool_calls else "stop"


def _normalize_call(raw: dict, index: int) -> dict:
    """把场景里的工具调用归一化成流式累积器能吃的形状（arguments 必须是 JSON 字符串）。"""
    raw = dict(raw or {})
    arguments = raw.get("arguments", {})
    wire = arguments if isinstance(arguments, str) else json.dumps(arguments or {}, ensure_ascii=False)
    return {
        "index": int(raw.get("index", index) or 0),
        "id": str(raw.get("id") or f"call_{index}"),
        "type": "function",
        "function": {"name": str(raw.get("name") or ""), "arguments": wire},
    }


def plan_from_mapping(raw: dict) -> ResponsePlan:
    """场景 dict → `ResponsePlan`。"""
    calls = raw.get("tool_calls")
    if calls is None:
        single = raw.get("tool_call")
        calls = [single] if single else []
    return ResponsePlan(
        text=str(raw.get("text") or ""),
        reasoning=str(raw.get("reasoning") or ""),
        tool_calls=[_normalize_call(call, index) for index, call in enumerate(calls)],
        finish_reason=str(raw.get("finish_reason") or ""),
        error=str(raw.get("error") or ""),
        raise_error=str(raw.get("raise") or ""),
    )


def load_plan(item: Any) -> ResponsePlan:
    """把场景脚本里的任一条目归一化成 `ResponsePlan`。"""
    if isinstance(item, ResponsePlan):
        return item
    if isinstance(item, str):
        return ResponsePlan(text=item)
    if isinstance(item, dict):
        if "choices" in item:
            return ResponsePlan(raw_body=item)
        return plan_from_mapping(item)
    if isinstance(item, (list, tuple)) and all(isinstance(event, StreamEvent) for event in item):
        return ResponsePlan(raw_events=list(item))
    raise EvalHarnessError(f"无法识别的 provider_script 条目：{item!r}")


@dataclass
class SeenRequest:
    """一次真实模型请求的观测快照。"""

    messages: list[dict]
    stream: bool
    payload: dict = field(default_factory=dict)

    @property
    def tool_messages(self) -> list[dict]:
        return [message for message in self.messages if message.get("role") == "tool"]


class ToolProbe:
    """评测用探针工具：记录调用顺序，返回可预测的文本。

    - `probe_a` / `probe_b` / `probe_c`：固定短文本；
    - `probe_fail`：以 ``error:`` 开头的失败结果（工具失败也要回填给模型）；
    - `probe_long`：超长文本（``arguments.size`` 指定长度，默认 60000），
      用来触发「工具结果 spill 落盘」路径。
    """

    def __init__(self, *, long_size: int = 60000) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.long_size = long_size

    async def _run(self, name: str, args: dict) -> str:
        args = dict(args or {})
        self.calls.append((name, args))
        if name == "probe_fail":
            return DEFAULT_TOOL_RESULTS["probe_fail"]
        if name == "probe_long":
            size = int(args.get("size") or self.long_size)
            head, tail = "LONG-HEAD-", "-LONG-TAIL"
            body = "x" * max(0, size - len(head) - len(tail))
            return f"{head}{body}{tail}"
        result = args.get("result")
        if result is not None:
            return str(result)
        return DEFAULT_TOOL_RESULTS.get(name, f"{name} 的结果")

    def specs(self) -> list[ToolSpec]:
        """探针工具清单（顺序固定，便于断言）。"""
        parameters = {"type": "object", "properties": {}}
        specs: list[ToolSpec] = []
        for name in PROBE_TOOLS:

            async def _handler(ctx, args, _name=name):  # noqa: ANN001 - ToolHandler 签名
                return await self._run(_name, args)

            specs.append(
                ToolSpec(
                    name=name,
                    description=f"eval 探针工具 {name}",
                    parameters=parameters,
                    handler=_handler,
                    scopes=("*",),
                )
            )
        return specs

    @property
    def names(self) -> list[str]:
        """工具执行顺序（按调用先后）。"""
        return [name for name, _args in self.calls]


class _Telemetry:
    """最小遥测门面：只收集，不落库。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.tools: list[Any] = []

    def record_call_simple(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)

    def record_tool(self, *args: Any, **kwargs: Any) -> None:
        self.tools.append((args, kwargs))

    def record_hook(self, *args: Any, **kwargs: Any) -> None:
        return None


class _Session:
    """最小会话门面（`run_turn` 写历史时会用到 `mark_replied`）。"""

    def __init__(self) -> None:
        self.replied = 0

    def mark_replied(self) -> None:
        self.replied += 1


def make_runtime(config: dict | None = None) -> SimpleNamespace:
    """构造评测用 runtime（只带跑一轮所需的最小属性）。"""
    merged = dict(DEFAULT_CONFIG)
    merged.update(config or {})
    runtime = SimpleNamespace(
        bot_id="10001",
        config=merged,
        invoker=None,
        bot=None,
        llm_tools=None,
        skills=None,
        memory=None,
        knowledge=None,
        mcp_manager=None,
        telemetry=_Telemetry(),
        provider_config=lambda: {"provider": "scripted", "model": "eval-model", "api_key": "sk-eval"},
        provider_chain=lambda: [{"provider": "scripted", "model": "eval-model"}],
        llm_hooks=None,
        llm_tool_call_hooks=None,
    )
    #: 本轮真实交给 `assembly.build_for` 的 PromptRequest（断言 kind 等）
    runtime._eval_prompt_requests = []
    return runtime


def _make_provider_class(
    plans: list[ResponsePlan],
    requests: list[SeenRequest],
    state: dict[str, bool],
) -> type[OpenAICompatProvider]:
    """生成一个"真实适配器 + 假 HTTP 底座"的 provider 类。"""

    class _ScriptedProvider(OpenAICompatProvider):
        def _next_plan(self) -> ResponsePlan:
            if not plans:
                state["exhausted"] = True
                return ResponsePlan(error="评测脚本已耗尽")
            return plans.pop(0)

        async def _request(self, payload, timeout):  # noqa: ANN001 - 与父类同名
            requests.append(
                SeenRequest(
                    messages=[dict(message) for message in payload.get("messages") or []],
                    stream=False,
                    payload=dict(payload),
                )
            )
            plan = self._next_plan()
            if plan.raise_error:
                raise RuntimeError(plan.raise_error)
            return plan.chat_body()

        async def _stream_once(self, api_key, payload, timeout):  # noqa: ANN001
            requests.append(
                SeenRequest(
                    messages=[dict(message) for message in payload.get("messages") or []],
                    stream=True,
                    payload=dict(payload),
                )
            )
            plan = self._next_plan()
            if plan.raise_error:
                # 流式路径不能真的抛：适配器会把"首事件前异常"当成可重试，
                # 于是同一条脚本被消费两次、请求次数断言失真。用 error 事件表达同样的语义。
                yield StreamEvent(type="error", text=plan.raise_error)
                return
            for event in plan.stream_events():
                yield event

    return _ScriptedProvider


async def run_scripted_turn(
    runtime: Any,
    *,
    user_text: str,
    stream_script: Sequence[Any] = (),
    abort: Any = None,
    chat_script: Sequence[Any] | None = None,
    config: dict | None = None,
    monkeypatch: pytest.MonkeyPatch | None = None,
    stream: bool = True,
    kind: str = "chat",
    history: Sequence[dict] | None = None,
    session_id: str = "group_1",
    is_private: bool = False,
    record_history: bool = True,
    long_tool_result_size: int = 60000,
) -> tuple[Any, list[SeenRequest]]:
    """跑一轮真实 `run_turn`，返回 `(TurnResult, requests_seen)`。

    Args:
        runtime: `make_runtime()` 造出来的 runtime（也接受自定义对象，但需要 `config`）。
        user_text: 本轮用户内容（空串会让 `from_request` 判为 blocked）。
        stream_script: 流式响应脚本（每条 = 一次模型响应）；`stream=False` 时若没给
            `chat_script`，会复用同一份脚本转成非流式响应体。
        chat_script: 非流式响应脚本；缺省复用 `stream_script`。
        config: 本轮要覆盖的配置键（合并进 `runtime.config`，便于逐轮微调）。
        monkeypatch: pytest 的 monkeypatch；不给就内部自建并在结束时 undo。
        stream: `True` 走流式（`_stream_once`），`False` 走阻塞路径（`_request`）。
        kind: 写进 `PromptRequest.kind` 的请求性质（chat / proactive / schedule / module）。
        history: 模拟"已经组装好的历史"，原样拼在本轮 user 消息之前。
        session_id / is_private / record_history: 透传给 `TurnRequest`。
        long_tool_result_size: `probe_long` 的默认长度。

    Returns:
        `(TurnResult, requests_seen)`；脚本多一条/少一条都会抛 `EvalHarnessError`
        （那说明场景的 `expect.requests` 写错了，不是被测代码的问题）。
    """
    import app.agent.loop.turn as turn_module
    from app.agent.loop.turn import TurnRequest

    mp = monkeypatch if monkeypatch is not None else pytest.MonkeyPatch()
    if config:
        runtime.config.update(config)

    chosen = stream_script if stream else (chat_script if chat_script is not None else stream_script)
    plans = [load_plan(item) for item in chosen]
    requests: list[SeenRequest] = []
    state = {"exhausted": False}
    probe = ToolProbe(long_size=long_tool_result_size)
    history_messages = [dict(message) for message in (history or [])]

    async def _fake_prepare(runtime_, event, ctx=None, *, session_mgr=None, turn=None, bot=None):  # noqa: ANN001
        prompt_req = SimpleNamespace(
            session_id=turn.session_id,
            kind="chat",
            config=runtime_.config,
            user_text=turn.user_text,
            extra_user_content_parts=[],
            modalities=None,
        )
        meta = {
            "model": "eval-model",
            "max_tokens": 128,
            "temperature": 0.7,
            "provider_chain": [{"provider": "scripted", "model": "eval-model"}],
            "modalities": None,
            "all_specs": probe.specs(),
            "tool_ctx": SimpleNamespace(
                extra={},
                runtime=runtime_,
                session_id=session_id,
                event=None,
            ),
            "session": _Session(),
            "session_mgr": None,
            "is_private": turn.is_private,
            "user_id": turn.user_id,
            "group_id": turn.group_id,
            "session_id": session_id,
        }
        return prompt_req, _Session(), meta

    def _build_for(runtime_, req):  # noqa: ANN001
        record = getattr(runtime_, "_eval_prompt_requests", None)
        if record is None:
            record = []
            runtime_._eval_prompt_requests = record
        record.append(req)
        return [*history_messages, {"role": "user", "content": req.user_text}]

    try:
        mp.setitem(PROVIDERS, "scripted", _make_provider_class(plans, requests, state))
        mp.setattr(turn_module.assembly, "build_for", _build_for)
        mp.setattr(turn_module, "prepare_prompt", _fake_prepare)
        result = await turn_module.run_turn(
            runtime,
            TurnRequest(
                session_id=session_id,
                is_private=is_private,
                user_text=user_text,
                kind=kind,
                stream=stream,
                record_history=record_history,
                abort=abort,
            ),
        )
    finally:
        if monkeypatch is None:
            mp.undo()

    if state["exhausted"]:
        raise EvalHarnessError(
            f"实际请求 {len(requests)} 次，超过 provider_script 的 {len(requests) - 1} 条"
        )
    if plans:
        raise EvalHarnessError(f"provider_script 还剩 {len(plans)} 条没被消费：请求次数比脚本少")
    return result, requests
