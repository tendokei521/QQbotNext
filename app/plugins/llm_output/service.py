"""输出策略适配器：把「模块配置」接到框架既有的输出实现上。

## 为什么用适配器而不是重写一套

框架的流式/非流式/发言通道实现（`StreamOutput` / `BlockingOutput` /
`SpeechOutbox`）已经过大量线上打磨（工具循环、打断、消息池节奏、部分历史 upsert）。
重写一遍必然漂移。

所以这里只做**一件事**：把模块自己的配置包成框架认识的配置对象（`ConfigView`），
再交给既有实现。于是：

- 机制仍在框架（一行没改）；
- 策略配置（`stream_*` / `speech_*`）搬到了模块；
- 卸载模块 → 槽回到 `None` → 框架按 agent 配置（或默认）走。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from app.agent.delivery.output import (
    BlockingOutput,
    ConfigView,
    StreamOutput,
)
from app.domain.message import Message


class OutputStrategyAdapter:
    """实现 `OutputStrategy` 契约：`respond(ctx, *, should_stop=None)`。

    Args:
        runtime: AgentRuntime（原有实现要用它取会话/工具/钩子）。
        config: 模块配置门面（`ModuleConfig`）。
        owned_keys: 本模块持有的键（其余键回退到 agent 配置）。
    """

    streams = False

    def __init__(self, runtime: Any, config: Any, owned_keys: Any) -> None:
        self.runtime = runtime
        self.module_config = config
        self.owned_keys = tuple(owned_keys or ())
        self._view = ConfigView(getattr(runtime, "config", None), config, self.owned_keys)

    # ── 模式判定 ────────────────────────────────────────────
    @property
    def mode(self) -> str:
        """``text`` / ``stream`` / ``speech`` 三选一。"""
        raw = str(self._get("mode", "text") or "text").strip().lower()
        if raw == "speech":
            return "speech"
        if raw == "stream":
            return "stream"
        # 兼容旧配置：只开了 stream_output 而没设 mode 的老用户
        return "stream" if bool(self._get("stream_output", False)) else "text"

    def _get(self, key: str, default: Any = None) -> Any:
        getter = getattr(self.module_config, "get", None)
        value = getter(key, default) if callable(getter) else default
        if value is not None:
            return value
        return getattr(self._view, "get")(key, default)

    # ── 契约实现 ────────────────────────────────────────────
    async def respond(
        self, ctx: Any, *, should_stop=None
    ) -> AsyncIterator[Message]:
        mode = self.mode
        self.streams = mode in ("stream", "speech")

        if mode == "text":
            strategy = BlockingOutput(self.runtime, self._view)
        else:
            # 发言通道（speech）同样走流式实现：它本来就需要"轮次边界"才能 flush 气泡
            strategy = StreamOutput(self.runtime, self._view)

        async for msg in strategy.respond(ctx, should_stop=should_stop):
            yield msg

    # ── 诊断 ────────────────────────────────────────────────
    def describe(self) -> dict:
        """当前生效形态（`/modules/features` 与排查用）。"""
        return {
            "mode": self.mode,
            "stream_send_pool_enabled": bool(self._get("stream_send_pool_enabled", False)),
            "speech_via_tool": bool(self._get("speech_via_tool", False)),
            "sentence_max_length": int(self._get("stream_sentence_max_length", 200) or 200),
            "owned_keys": len(self.owned_keys),
        }
