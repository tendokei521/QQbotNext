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

- `BlockingOutput`：非流式（`generate_response` + `post_response` 钩子 + 输出指令解析）；
- `StreamOutput`：流式（`stream_response` 逐句 + 可选消息池节奏）；
- `select_default_output(config)`：**框架默认策略**，按 `stream_output` 选择。

模块可 `supersedes = ("output",)` 并提供自己的 `respond()` 整体替换。
框架默认策略仍然读 `stream_output` 配置——**在模块接管之前，行为逐字不变**。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any, Protocol, runtime_checkable

from app.agent.delivery.directives import build_outbound_builder
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

    def __init__(self, runtime: Any = None) -> None:
        self.runtime = runtime

    async def respond(
        self, ctx: Any, *, should_stop: Callable[[], bool] | None = None
    ) -> AsyncIterator[Message]:
        from app.agent.assembly.assembler import apply_post_assemble  # noqa: F401 (文档用途)
        from app.agent.hooks import run_stage
        from app.agent.loop.generate import generate_response

        runtime = self.runtime or getattr(ctx, "runtime", None)
        config = getattr(runtime, "config", None)

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
            return
        job = getattr(ctx, "job", None)
        if job is not None and (job.skip or job.superseded):
            return
        if not ctx.response_messages:
            ctx.response_messages = [Message.from_text(ctx.response_text)]

        for raw in list(ctx.response_messages):
            msg = raw if isinstance(raw, Message) else Message.from_text(str(raw))
            if getattr(msg, "skip", False):
                continue
            yield msg


class StreamOutput:
    """流式输出：逐句产出，可选经消息池按节奏放行。

    ``stream_send_pool_enabled`` 打开时，句子进入 `StreamSendPool`——
    池负责发送间隔/前后缀/队列策略，本策略从池的队列里取出并 yield。
    """

    streams = True

    def __init__(self, runtime: Any = None) -> None:
        self.runtime = runtime

    async def respond(
        self, ctx: Any, *, should_stop: Callable[[], bool] | None = None
    ) -> AsyncIterator[Message]:
        runtime = self.runtime or getattr(ctx, "runtime", None)
        config = getattr(runtime, "config", None)
        pool_enabled = bool(
            config.get("stream_send_pool_enabled", False) if _has_get(config) else False
        )
        if not pool_enabled:
            async for msg in self._respond_direct(ctx, should_stop):
                yield msg
            return
        async for msg in self._respond_via_pool(ctx, should_stop):
            yield msg

    async def _respond_direct(
        self, ctx: Any, should_stop: Callable[[], bool] | None
    ) -> AsyncIterator[Message]:
        from app.agent.loop.chat import stream_response

        runtime = self.runtime or getattr(ctx, "runtime", None)
        config = getattr(runtime, "config", None)
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
        config = getattr(runtime, "config", None)
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
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass


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


def _has_get(config: Any) -> bool:
    return callable(getattr(config, "get", None))


def select_default_output(config: Any = None) -> OutputStrategy:
    """框架默认输出策略：按 ``stream_output`` 选择（模块接管前的既有行为）。"""
    stream = False
    if _has_get(config):
        try:
            stream = bool(config.get("stream_output", False))
        except Exception:  # noqa: BLE001 —— 配置异常按非流式处理（保守）
            stream = False
    return StreamOutput() if stream else BlockingOutput()


def resolve_output(runtime: Any) -> OutputStrategy:
    """取该运行时的输出策略；缺失或不合格时回退默认（与 invoker 同口径）。"""
    strategy = getattr(runtime, "output", None)
    if isinstance(strategy, OutputStrategy) and callable(getattr(strategy, "respond", None)):
        return strategy
    if strategy is not None and callable(getattr(strategy, "respond", None)):
        return strategy
    if strategy is not None:
        from app.core.logger import logger

        logger.add_info(f"#{getattr(runtime, 'bot_id', '?')}").warning(
            f"[Output] runtime.output 不可用（{type(strategy).__name__}），已回退默认输出策略"
        )
    return select_default_output(getattr(runtime, "config", None))
