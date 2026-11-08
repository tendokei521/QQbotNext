"""输出策略槽：一轮回复**怎么产出**（一次性 / 逐句流式 / 发言通道）。

## 解决的问题

"流式还是非流式"此前是散落在 3 个文件里的**配置分支**：

```
app/agent/loop/pipeline.py:310        if config.get("stream_output"): _run_stream() else: generate_response()
app/agent/proactivity/manager.py:318  同一判断
app/agent/proactivity/scheduler.py:286 同一判断
```

于是「把流式做成模块」这件事做不了——没有槽，只能改配置或改源码。

## 契约

```python
class OutputStrategy(Protocol):
    async def respond(self, ctx, *, should_stop=None) -> AsyncIterator[Message]:
        # 产出一轮回复的全部待发消息（顺序即发送顺序）
        # - 不发送：发送由流水线/交付层负责（pre_send → send → post_send）
        # - should_stop() 返回 True 时尽快停止产出（回复打断）
        # - 产出的每条 Message 已是最终形态（指令已解析、段已组装）
```

## 现状

三个**正交**的维度：

| | 说明 | 谁决定 |
|---|---|---|
| **通道** | 文本即回复 / 发言通道（文本=思考，发言走工具） | 回复模式（`reply_mode`） |
| **传输** | 流式（边收边用）/ 一次拿全 | `stream_output` |
| **发言方式** | 整段一起发 / 收到就发 / 拆分节奏发 | `reply_send_mode`（见 `send_mode.py`） |

- `BlockingOutput`：文本即回复 + 一次拿全；发言方式 `split` 时按换行/
  句末标点拆开，逐条按节奏发送（非流式也能"分几条说"）；
- `StreamOutput`：文本即回复 + 流式；`whole` 先攒着结束发一条，`realtime`
  每切出一句立刻发，`split` 每句进消息池按节奏放行；
- `SpeechOutput(stream=…)`：发言通道，**两种传输都支持**（流式=轮次边界 flush，非流式=整轮结束统一 flush）；
  发言方式不作用于发言通道——气泡条数由 `send_message` / 气泡约束决定；
- `select_default_output(config, runtime, ctx)`：按通道与传输选策略，发言方式在策略内部生效。

> 历史坑：发言通道曾经**寄生在流式里**（`capability.streams` 把 speech 也算流式），
> 结果是"没开流式却被强制走流式请求"，而且非流式路径根本没有 flush 气球的实现。
> 现在通道与传输拆开，四种组合都成立。

模块可 `supersedes = ("output",)` 并提供自己的 `respond()` 整体替换；
参与度模式（发言通道）由框架直接决定，模块策略只在 legacy 生效。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any, Protocol, runtime_checkable

from app.agent.delivery.directives import build_outbound_builder
from app.agent.delivery.send_mode import (
    SEND_MODE_SPLIT,
    SEND_MODE_WHOLE,
    resolve_send_mode,
    split_messages,
)
from app.core import diagnostics as diag
from app.core.logger import account_label, logger
from app.domain.message import Message


@runtime_checkable
class OutputStrategy(Protocol):
    """输出策略契约（结构类型：任何有 ``respond`` 的对象都能接管）。"""

    def respond(
        self, ctx: Any, *, should_stop: Callable[[], bool] | None = None
    ) -> AsyncIterator[Message]: ...


class BlockingOutput:
    """非流式输出：一次拿全，交给 `post_response` 钩子改写后再发。"""

    streams = False

    def __init__(self, runtime: Any = None, config: Any = None) -> None:
        self.runtime = runtime
        #: 可选配置视图。模块接管输出时把自己模块的配置传进来，
        #: 于是这些策略"读的是模块配置"，框架自身不再需要持有那些键。
        self.config = config

    async def respond(
        self, ctx: Any, *, should_stop: Callable[[], bool] | None = None
    ) -> AsyncIterator[Message]:
        from app.agent.assembly.assembler import apply_post_assemble  # noqa: F401 (文档用途)
        from app.agent.hooks import run_stage
        from app.agent.loop.generate import generate_response

        runtime = self.runtime or getattr(ctx, "runtime", None)
        config = self.config if self.config is not None else getattr(runtime, "config", None)

        response_text = await generate_response(runtime, ctx.event, ctx)
        if response_text is None:
            return
        if should_stop is not None and should_stop():
            return

        ctx.response_text = response_text
        # 输出侧动作通道：把 [reply]/[@QQ] 指令转成真正的消息段（无指令时等价纯文本）
        outbound = build_outbound_builder(ctx.event, config).build(response_text)
        if outbound is None:
            # 只有指令、没有正文：不发空消息
            return
        ctx.response_messages = [outbound]

        if not await run_stage(runtime, "post_response", ctx):
            # 钩子否决 = 整条回复不发。用户侧是"机器人没理我"，必须能查到是谁拦的。
            diag.log_failure(
                _failure_log(ctx),
                "delivery.post_response_vetoed",
                "post_response 阶段否决，整条回复未发送",
                fields={
                    **_failure_fields(ctx),
                    "chars": len(str(response_text or "")),
                },
            )
            return
        job = getattr(ctx, "job", None)
        if job is not None and (job.skip or job.superseded):
            return
        if not ctx.response_messages:
            ctx.response_messages = [Message.from_text(ctx.response_text)]

        messages = list(ctx.response_messages)
        # 发言方式：非流式传输下 `realtime` 没有"边收"可谈，解析后退化为整段。
        if resolve_send_mode(config, transport_stream=False) == SEND_MODE_SPLIT:
            async for msg in _iter_split_delivery(
                messages, config, runtime, ctx, should_stop
            ):
                yield msg
            return

        for raw in messages:
            msg = raw if isinstance(raw, Message) else Message.from_text(str(raw))
            if getattr(msg, "skip", False):
                continue
            yield msg


class StreamOutput:
    """流式输出：文本边收边用，按「发言方式」决定发送形态。

    - ``whole``：边收只攒着，本轮结束发一条（指令/引用按整段解析）；
    - ``realtime``：每切出一句立刻 yield，不额外等待；
    - ``split``：每句进入 `StreamSendPool`——池负责发送间隔/前后缀/队列策略，
      本策略从池的队列里取出并 yield。
    """

    streams = True

    def __init__(self, runtime: Any = None, config: Any = None) -> None:
        self.runtime = runtime
        #: 可选配置视图（模块接管时传自己模块的配置，见 BlockingOutput）
        self.config = config

    def _cfg(self, ctx: Any) -> Any:
        if self.config is not None:
            return self.config
        runtime = self.runtime or getattr(ctx, "runtime", None)
        return getattr(runtime, "config", None)

    async def respond(
        self, ctx: Any, *, should_stop: Callable[[], bool] | None = None
    ) -> AsyncIterator[Message]:
        config = self._cfg(ctx)
        mode = resolve_send_mode(config, transport_stream=True)
        if mode == SEND_MODE_WHOLE:
            async for msg in self._respond_whole(ctx, should_stop):
                yield msg
            return
        if mode == SEND_MODE_SPLIT:
            async for msg in self._respond_via_pool(ctx, should_stop):
                yield msg
            return
        async for msg in self._respond_direct(ctx, should_stop):
            yield msg

    async def _respond_whole(
        self, ctx: Any, should_stop: Callable[[], bool] | None
    ) -> AsyncIterator[Message]:
        """整段一起发：流式边收边攒，本轮结束才构造一条消息。

        发言通道的气泡（``Message`` 产出物）不参与合并——它们本身就是模型
        决定要发的独立气泡，攒起来会把多条发言揉成一条。
        """
        from app.agent.loop.chat import stream_response

        runtime = self.runtime or getattr(ctx, "runtime", None)
        config = self._cfg(ctx)
        outbound = build_outbound_builder(ctx.event, config)
        parts: list[str] = []
        async for item in stream_response(runtime, ctx.event, ctx):
            if should_stop is not None and should_stop():
                # 整段模式还没发出去任何东西：与流式打断同语义，剩余不再发送。
                _mark_interrupted(ctx)
                return
            if isinstance(item, Message):
                if item.segments:
                    yield item
                continue
            parts.append(str(item or ""))

        text = "".join(parts).strip()
        if not text:
            return
        msg = outbound.build(text)
        if msg is not None:
            yield msg

    async def _respond_direct(
        self, ctx: Any, should_stop: Callable[[], bool] | None
    ) -> AsyncIterator[Message]:
        from app.agent.loop.chat import stream_response

        runtime = self.runtime or getattr(ctx, "runtime", None)
        config = self._cfg(ctx)
        outbound = build_outbound_builder(ctx.event, config)
        async for item in stream_response(runtime, ctx.event, ctx):
            if should_stop is not None and should_stop():
                _mark_interrupted(ctx)
                return
            msg = as_message(item, outbound)
            if msg is not None:
                yield msg

    async def _respond_via_pool(
        self, ctx: Any, should_stop: Callable[[], bool] | None
    ) -> AsyncIterator[Message]:
        """池化路径：池控节奏（sleep/前后缀/队列策略），这里只负责取出并 yield。"""
        from app.agent.delivery.pool import StreamSendPool
        from app.agent.loop.chat import stream_response

        runtime = self.runtime or getattr(ctx, "runtime", None)
        config = self._cfg(ctx)
        outbound = build_outbound_builder(ctx.event, config)
        queue: asyncio.Queue[Message | None] = asyncio.Queue()
        done = asyncio.Event()

        async def _put(msg: Message) -> None:
            # 池在「停顿 + 前后缀」之后调用这里，因此节奏已经生效
            await queue.put(msg)

        pool = StreamSendPool(
            config,
            send_message=_put,
            should_cancel=lambda: bool(should_stop and should_stop()),
        )

        async def _producer() -> None:
            try:
                async for item in stream_response(runtime, ctx.event, ctx):
                    if should_stop is not None and should_stop():
                        _mark_interrupted(ctx)
                        break
                    msg = as_message(item, outbound)
                    if msg is not None:
                        await pool.put(msg)
            finally:
                try:
                    await pool.finish()
                    await pool.wait_drained()
                finally:
                    await pool.shutdown()
                    done.set()
                    await queue.put(None)

        task = asyncio.create_task(_producer())
        try:
            while True:
                msg = await queue.get()
                if msg is None:
                    break
                yield msg
        finally:
            # 区分"被取消"与"生产者真的失败"：旧实现 `except (CancelledError, Exception): pass`
            # 把 stream_response 的真实异常完全吞掉——整轮无输出、日志零痕迹。
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:  # noqa: BLE001 —— 记录后照常结束本轮
                diag.log_failure(
                    _failure_log(ctx),
                    "delivery.producer_failed",
                    "流式生产者异常，本轮回复可能不完整甚至为空",
                    error=e,
                    fields=_failure_fields(ctx),
                    with_traceback=True,
                )


def as_message(item: Any, outbound: Any = None) -> Message | None:
    """把流式产出物统一成待发送 ``Message``。

    两条流式语义并存：

    - **文本即发言**（旧）：``stream_response`` 逐句 yield 字符串，由 ``outbound``
      解析首片的 ``[reply]``/``[@QQ]`` 指令；
    - **发言通道**（``speech_via_tool``）：yield 的已经是构造好的 ``Message``
      （气泡自带引用/@ 段），不能再过一遍指令解析 —— 否则正文里的 ``[...]`` 会被二次剥离。
    """
    if isinstance(item, Message):
        return item if item.segments else None
    if outbound is None:
        text = str(item or "").strip()
        return Message.from_text(text) if text else None
    return outbound.build(str(item or ""))


def _mark_interrupted(ctx: Any) -> None:
    state = getattr(ctx, "state", None)
    if isinstance(state, dict):
        state["interrupted"] = True


def _max_message_length(config: Any, runtime: Any) -> int:
    """拆分发送时单片文本的上限（与流式切句共用 ``stream_sentence_max_length``）。"""
    for cfg in (config, getattr(runtime, "config", None)):
        for key in ("stream_sentence_max_length", "max_message_length"):
            value = _safe_get(cfg, key, None)
            try:
                if value:
                    return max(10, int(value))
            except (TypeError, ValueError):
                continue
    return 200


async def _iter_split_delivery(
    messages: list[Any],
    config: Any,
    runtime: Any,
    ctx: Any,
    should_stop: Callable[[], bool] | None,
) -> AsyncIterator[Message]:
    """「拆分节奏」的非流式投递：整段拿到后切片，按发送节奏逐条产出。

    与流式消息池同一口径：**第一条不等待**，之后每条发送前按 `SendPolicy`
    等待（间隔模式 ``none`` 时不等待，等于连续发送）。发送前后钩子由流水线负责。
    """
    from app.domain.pace import SendPolicy

    # post_response 钩子可能把整条消息标成 skip（否决发送）：拆分前先剔掉，
    # 否则拆出来的新 Message 不继承 skip 标记，反而会把它发出去。
    messages = [msg for msg in messages if not getattr(msg, "skip", False)]
    pieces = split_messages(
        messages,
        max_length=_max_message_length(config, runtime),
        include_affix=config,
    )
    policy = SendPolicy(config if config is not None else {})
    sent_any = False
    for msg in pieces:
        if getattr(msg, "skip", False):
            continue
        if should_stop is not None and should_stop():
            _mark_interrupted(ctx)
            return
        if sent_any:
            delay = policy.next_delay(msg)
            if delay > 0:
                await asyncio.sleep(delay)
        sent_any = True
        yield msg


def _failure_log(ctx: Any):
    """输出侧失败日志器：带上账号前缀（多账号时能分清是谁没发出去）。"""
    runtime = getattr(ctx, "runtime", None)
    bot_id = getattr(runtime, "bot_id", "")
    return logger.add_info(account_label(bot_id))


def _failure_fields(ctx: Any) -> dict[str, Any]:
    """输出侧失败的定位字段（会话 id 在 ctx 上，别让日志只剩一句异常）。"""
    runtime = getattr(ctx, "runtime", None)
    return {
        "bot": getattr(runtime, "bot_id", ""),
        "session": getattr(ctx, "session_id", ""),
    }



def _transport_stream(config: Any, runtime: Any = None) -> bool:
    """请求/响应方式：``True`` = 流式（边收边用），``False`` = 一次拿全。

    **声明优先，配置兜底**：模块用 ``runtime.capabilities.declare_output`` 声明自己能
    流式（``stream``）还是只能整段（``blocking``）时，以声明为准；没声明、或声明的是
    发言通道（``speech``，它不决定传输形态）才读 ``stream_output``。这样"声明决定能力、
    配置决定偏好"在传输这一维也成立，不会出现"模块只能整段、配置却逼它流式"。
    """
    config_stream = False
    getter = getattr(config, "get", None)
    if callable(getter):
        try:
            config_stream = bool(getter("stream_output", False))
        except Exception:  # noqa: BLE001 —— 配置坏了按非流式（最保守的传输方式）
            config_stream = False

    from app.agent.capabilities import OUTPUT_BLOCKING, OUTPUT_STREAM, CapabilityRegistry

    registry = getattr(runtime, "capabilities", None)
    if isinstance(registry, CapabilityRegistry) and registry.output is not None:
        declared = registry.output
        if declared.kind in (OUTPUT_STREAM, OUTPUT_BLOCKING):
            return declared.kind == OUTPUT_STREAM
    return config_stream


class SpeechOutput:
    """发言通道策略：文本只是思考，真正的发言由 ``send_message`` 入稿。

    **通道 ≠ 传输**：`stream` 只决定请求方式，与"文本发不发言"无关。

    - ``stream=True``：走流式循环，在**轮次边界** flush 气泡（想一句说一句，体感最像人）；
    - ``stream=False``：走一次拿全的非流式请求，本轮结束后**一次性** flush 气泡。

    历史实现把发言通道硬绑在流式上（`capability.streams` 把 speech 也算流式），
    于是"没开流式却走了流式请求"。这里把两者拆开。
    """

    def __init__(self, runtime: Any = None, config: Any = None, *, stream: bool = True) -> None:
        self.runtime = runtime
        self.config = config
        self.stream = bool(stream)

    @property
    def streams(self) -> bool:
        """是否逐句产出（决定流水线要不要跑 post_stream / 打断语义）。"""
        return self.stream

    async def respond(self, ctx: Any, *, should_stop: Callable[[], bool] | None = None):
        if self.stream:
            inner = StreamOutput(self.runtime, self.config)
            async for msg in inner.respond(ctx, should_stop=should_stop):
                yield msg
            return

        from app.agent.loop.assistant_output import flush_speech
        from app.agent.loop.generate import generate_response

        runtime = self.runtime or getattr(ctx, "runtime", None)
        config = self.config if self.config is not None else getattr(runtime, "config", None)

        # 非流式：没有"轮次边界"可以 flush，所以让 send_message 入稿即发送
        # （`SpeechOutbox.immediate` → 工具 handler 自己发）。文本仍只是思考。
        state = getattr(ctx, "state", None)
        if isinstance(state, dict):
            state["speech_immediate"] = True

        # 跑完一轮（工具循环里 send_message 会把气泡入稿并直接发出）
        # 文本=思考，**不写历史、不发出去**：历史由发送方按气泡写，
        # 否则"想过但没发"的正文会被落成一条用户看不到的 assistant 消息。
        try:
            await generate_response(
                runtime, getattr(ctx, "event", None), ctx, record_history=False
            )
        except Exception as e:  # noqa: BLE001 —— 单轮失败仍要把已入稿的气泡发出去
            from app.core.logger import account_label, logger

            logger.add_info(account_label(getattr(runtime, "bot_id", "?"))).warning(
                f"[Speech] 非流式轮次失败（仍会发送已入稿的气泡）: {e}"
            )

        speech = (getattr(ctx, "state", None) or {}).get("speech_outbox")
        if speech is None:
            return
        session_mgr = getattr(runtime, "session_mgr", None)
        if session_mgr is None:
            from app.agent.session.manager import SessionManager

            session_mgr = SessionManager(str(getattr(runtime, "bot_id", "")))
        session_id = str(getattr(ctx, "session_id", "") or "")
        spoken = 0
        async for message in flush_speech(
            speech, session_mgr, session_id, config, getattr(ctx, "event", None)
        ):
            spoken += 1
            yield message
        # 思考内容在"发完之后"才齐（一轮的 reasoning 由 tool_step 累积）：
        # 把它补到最后一条 assistant 历史上，会话数据页才能展开溯源。
        reasoning = str((getattr(ctx, "state", None) or {}).get("reasoning_text") or "").strip()
        setter = getattr(session_mgr, "set_last_assistant_reasoning", None)
        if spoken:
            if reasoning and callable(setter):
                setter(session_id, reasoning)
        elif reasoning:
            # 未回复轮：正文记（未回复），思考保留可展开；silent 条目不进模型上下文。
            session_mgr.add_message(
                session_id, "assistant", "（未回复）", reasoning=reasoning, silent=True,
            )
            session = session_mgr.get_session(session_id)
            if session is not None:
                import asyncio

                await asyncio.to_thread(session_mgr.history.save_session, session)


def _ctx_is_private(ctx: Any) -> bool:
    """本轮是否私聊：优先流水线写入的事实，其次事件协议字段，最后会话前缀。"""
    state = getattr(ctx, "state", None)
    if isinstance(state, dict) and "is_private" in state:
        return bool(state.get("is_private"))
    event = getattr(ctx, "event", None)
    if event is not None:
        from app.domain.events import is_private_event

        return is_private_event(event)
    return str(getattr(ctx, "session_id", "") or "").startswith("private_")


def select_default_output(config: Any = None, runtime: Any = None, ctx: Any = None) -> OutputStrategy:
    """框架默认输出策略：**通道**（发言通道 / 文本即回复）× **传输**（流式 / 一次拿全）。

    这两个维度是正交的，四种组合都成立：

    | 通道 | 传输 | 策略 |
    |---|---|---|
    | 文本即回复 | 一次拿全 | `BlockingOutput` |
    | 文本即回复 | 流式 | `StreamOutput` |
    | 发言通道 | 流式 | `SpeechOutput(stream=True)` |
    | 发言通道 | 非流式 | `SpeechOutput(stream=False)` |

    **发言方式**（`reply_send_mode`：整段 / 收到就发 / 拆分节奏）不参与选策略，
    由 `BlockingOutput` / `StreamOutput` 在 ``respond()`` 内部按配置生效；
    发言通道的气泡已由模型逐条决定，不受发言方式改写。

    通道由**回复模式**决定（`reply_mode=participation` ⇒ 发言通道），**但仅限群聊**；
    私聊永远文本即回复（`reply_mode` 在私聊不生效）。传输由 `stream_output` 决定。
    模块声明（`capabilities`）只在群聊 legacy 且没有上下文时参与，
    不再能把发言通道改回"文本即回复"，也不能把私聊变成发言通道。
    """
    from app.agent.capabilities import output_capability
    from app.agent.delivery.speech import speech_via_tool

    is_private = _ctx_is_private(ctx) if ctx is not None else False
    participation_turn = bool(
        ctx is not None and getattr(ctx, "state", None) and ctx.state.get("participation_gate")
    )
    # 参与度门只在群聊打开（pipeline 只对 message_group 过门）；私聊一律普通输出。
    speech = participation_turn or speech_via_tool(config, is_private=is_private)
    transport = _transport_stream(config, runtime)
    if speech:
        return SpeechOutput(runtime, config, stream=transport)
    capability = output_capability(runtime, config, is_private=is_private)
    if capability.speech and not is_private:  # 声明了发言通道（模块/配置），同样尊重传输开关
        return SpeechOutput(runtime, config, stream=transport)
    return StreamOutput(runtime, config) if transport else BlockingOutput(runtime, config)


class ConfigView:
    """把「模块配置」包成框架策略认识的配置对象。

    框架的流式/非流式/发言通道实现读的是"某个配置对象"。把模块自己的配置包一层
    交给它，**机制完全不用改**，而那些 ``stream_*`` / ``speech_*`` 键就从
    agent 配置搬到了模块配置里。

    未在 ``owned_keys`` 里的键回退到 ``base``（agent 配置）—— 这样迁移期间
    框架仍能读到尚未迁走的键，行为不变。
    """

    def __init__(self, base: Any, module_config: Any, owned_keys: Any) -> None:
        self._base = base
        self._module = module_config
        self._owned = frozenset(owned_keys or ())

    #: 本视图**拥有**的键（其余键只是回退到 base 的透传，不算回答）。
    #: ``ChainedConfig`` 靠它判断"这个视图该不该参与该键的仲裁"——否则排在最前的
    #: 视图会把所有键都用 agent 旧值答掉，后面模块的配置永远读不到（实测复现过）。
    @property
    def owned_keys(self) -> frozenset[str]:
        return self._owned

    def owns(self, key: str) -> bool:
        return key in self._owned

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._owned:
            value = _safe_get(self._module, key, None)
            if value is not None:
                return value
            # 模块侧没给（旧配置/未迁移）→ 退回 agent 配置，保持升级平滑
        return _safe_get(self._base, key, default)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)


def _safe_get(config: Any, key: str, default: Any) -> Any:
    getter = getattr(config, "get", None)
    if not callable(getter):
        return default
    try:
        return getter(key, default)
    except Exception:  # noqa: BLE001
        return default


def resolve_output(runtime: Any, ctx: Any = None) -> OutputStrategy:
    """取该运行时的输出策略；缺失或不合格时回退默认（与 invoker 同口径）。

    ``ctx`` 参与判定：参与度模式（发言通道）由回复模式决定，模块声明的输出形态
    不能把它改回"文本即回复"（否则整轮无输出——发言都记在 outbox 里没人 flush）。
    """
    strategy = getattr(runtime, "output", None)
    if isinstance(strategy, OutputStrategy) and callable(getattr(strategy, "respond", None)):
        # 私聊永远普通输出：模块策略（含发言通道实现）不能改写场景语义。
        if _ctx_is_private(ctx) and (isinstance(strategy, SpeechOutput) or getattr(strategy, "speech", False)):
            view = getattr(strategy, "config_view", None) or getattr(runtime, "config", None)
            return select_default_output(view, runtime, ctx)
        # 模块接管：参与度模式下仍需框架的发言通道语义——模块策略只在 legacy 生效
        participation_turn = bool(
            ctx is not None
            and getattr(ctx, "state", None)
            and ctx.state.get("participation_gate")
        )
        if not participation_turn:
            return strategy
        # 参与度轮次：策略实现由框架给（模块只能贡献传输参数），所以用模块的配置视图
        # 让 `stream_output` / 气泡约束仍是模块那份——否则"模块开了流式、account 没开"
        # 会被静默降成非流式（另一种形态错位）。
        view = getattr(strategy, "config_view", None) or getattr(runtime, "config", None)
        return select_default_output(view, runtime, ctx)
    if strategy is not None and callable(getattr(strategy, "respond", None)):
        return strategy
    if strategy is not None:
        from app.core.logger import logger

        logger.add_info(account_label(getattr(runtime, 'bot_id', '?'))).warning(
            f"[Output] runtime.output 不可用（{type(strategy).__name__}），已回退默认输出策略"
        )
    return select_default_output(getattr(runtime, "config", None), runtime, ctx)
