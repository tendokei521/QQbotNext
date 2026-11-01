"""输出策略适配器：把「模块配置」接到框架的输出策略上。

框架已经实现了「通道 × 传输」四种组合（`BlockingOutput` / `StreamOutput` /
`SpeechOutput`），本模块只做两件事：

1. 把模块自己的配置包成框架认识的配置对象（`ConfigView`）；
2. 把**策略选择**交回框架（`select_default_output`）——通道由回复模式决定，
   传输由 `stream_output` 决定，模块不再自己判定形态。

历史实现有自己的 `mode`（text/stream/speech）三选一，和框架的"通道 × 传输"
两个维度重复，且把发言通道硬绑在流式上。`mode` 已删除。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from app.agent.delivery.output import ConfigView
from app.domain.message import Message

from .src.output_mode import OutputOptionsView


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

    # ── 传输判定（唯一事实源 = `stream_output`；不再有 mode 三选一） ──
    @property
    def streams(self) -> bool:
        """输出策略是否逐条产出（流水线据此决定 post_stream / 打断语义）。"""
        return bool(self._get("stream_output", False))

    def options_view(self) -> OutputOptionsView:
        """给装配/提示词层读的策略视图（``runtime.output_options``）。"""
        return OutputOptionsView(
            getattr(self.runtime, "config", None), self.module_config, self.owned_keys
        )

    @property
    def config_view(self) -> ConfigView:
        """本模块的配置视图（框架选策略时用它读 ``stream_*`` 参数）。"""
        return self._view

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
        """把「通道 × 传输」的选择交给框架，本模块只提供配置视图。

        以前这里自己按 `mode` 选 Blocking/Stream，于是"通道"（发言通道）
        也被模块决定了。现在只有框架知道回复模式，模块只贡献 `stream_*` 参数。
        """
        from app.agent.delivery.output import select_default_output

        strategy = select_default_output(self._view, self.runtime, ctx)
        async for msg in strategy.respond(ctx, should_stop=should_stop):
            yield msg

    # ── 诊断 ────────────────────────────────────────────────
    def describe(self) -> dict:
        """当前生效形态（`/modules/features` 与排查用）。"""
        strategy = None
        try:
            from app.agent.delivery.output import select_default_output

            strategy = select_default_output(self._view, self.runtime, None)
        except Exception:  # noqa: BLE001 —— 诊断失败不该影响模块
            strategy = None
        return {
            "transport": "stream" if self.streams else "blocking",
            "strategy": type(strategy).__name__ if strategy is not None else "",
            "stream_send_pool_enabled": bool(self._get("stream_send_pool_enabled", False)),
            "sentence_max_length": int(self._get("stream_sentence_max_length", 200) or 200),
            "owned_keys": len(self.owned_keys),
        }
