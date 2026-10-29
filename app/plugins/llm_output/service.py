"""输出策略适配器：把「模块配置」接到框架既有的输出实现上。

框架的流式/非流式/发言通道实现（`StreamOutput` / `BlockingOutput` / `SpeechOutbox`）
已经过大量线上打磨（工具循环、打断、消息池节奏、部分历史 upsert），重写必然漂移。
所以这里只做**一件事**：把模块自己的配置包成框架认识的配置对象（`ConfigView`），
再交给既有实现。于是机制仍在框架（一行没改）、策略配置搬到了模块。

形态判定（`mode` 唯一事实源 + 旧开关迁移）见 `src/output_mode.py`。
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

from .src.output_mode import OutputOptionsView, resolve_mode


class OutputStrategyAdapter:
    """实现 `OutputStrategy` 契约：`respond(ctx, *, should_stop=None)`。

    Args:
        runtime: AgentRuntime（原有实现要用它取会话/工具/钩子）。
        config: 模块配置门面（`ModuleConfig`）。
        owned_keys: 本模块持有的键（其余键回退到 agent 配置）。
    """

    def __init__(self, runtime: Any, config: Any, owned_keys: Any) -> None:
        self.runtime = runtime
        self.module_config = config
        self.owned_keys = tuple(owned_keys or ())
        self._view = ConfigView(getattr(runtime, "config", None), config, self.owned_keys)

    # ── 模式判定（唯一事实源见 src/output_mode.py） ──────────
    @property
    def mode(self) -> str:
        return resolve_mode(self.module_config)

    @property
    def effective_speech(self) -> bool:
        """本轮是不是发言通道（文本=思考，发言走 send_message）。"""
        return self.mode == "speech"

    @property
    def streams(self) -> bool:
        """输出策略是否逐条产出（流水线据此决定 post_stream / 打断语义）。"""
        return self.mode in ("stream", "speech")

    def options_view(self) -> OutputOptionsView:
        """给装配/提示词层读的策略视图（``runtime.output_options``）。"""
        return OutputOptionsView(
            getattr(self.runtime, "config", None), self.module_config, self.owned_keys
        )

    def _get(self, key: str, default: Any = None) -> Any:
        getter = getattr(self.module_config, "get", None)
        value = getter(key, default) if callable(getter) else default
        if value is not None:
            return value
        return self._view.get(key, default)

    # ── 契约实现 ────────────────────────────────────────────
    async def respond(
        self, ctx: Any, *, should_stop=None
    ) -> AsyncIterator[Message]:
        mode = self.mode

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
            "speech": self.effective_speech,
            "stream_send_pool_enabled": bool(self._get("stream_send_pool_enabled", False)),
            "sentence_max_length": int(self._get("stream_sentence_max_length", 200) or 200),
            "owned_keys": len(self.owned_keys),
        }
