"""LLM 输出策略模块：**流式输出 + 发言通道**（原 agent 配置页的两组键搬到这里）。

## 它接管什么

框架的 `output` 槽（`runtime.output`）。此前"流式还是非流式"是散落在
3 个文件里的 `config.get("stream_output")` 分支，模块只能改配置或改源码。
现在这个模块把整块策略接过来：

| mode | 行为 |
|---|---|
| `text` | 一次性拿全再发（框架默认的 `BlockingOutput`） |
| `stream` | 按句流式发送，可经消息池控节奏（`StreamOutput`） |
| `speech` | 文本=思考，发言走 `send_message` 工具（发言通道，需流式） |

## 配置归属

`config_partition.MODULE_KEYS["llm_output"]` 里的 25 个键（流式 19 + 发言 6）
由本模块持有。框架侧通过 `ConfigView` 把它们"喂"给既有的流式/非流式实现——
机制不改，配置搬家。

> 迁移期兼容：模块配置里没有的键会回退到 agent 配置（`ConfigView` 的 base），
> 所以老用户升级后行为不变；等 agent 侧那些键删掉后，就完全以模块配置为准。
"""

from __future__ import annotations

from typing import Any

from app.plugin import BaseModule

from .config_schema import SCHEMA
from .service import OutputStrategyAdapter

#: 本模块持有的键（与 `app/agent/config_partition.py::MODULE_KEYS["llm_output"]` 一致）
OWNED_KEYS: tuple[str, ...] = (
    "stream_output",
    "stream_send_pool_enabled",
    "stream_send_by_sentence",
    "stream_sentence_max_length",
    "stream_send_interval_mode",
    "stream_send_interval_base_ms",
    "stream_send_interval_min_ms",
    "stream_send_interval_max_ms",
    "stream_send_curve",
    "stream_send_curve_k",
    "stream_short_message_length",
    "stream_short_message_delay_ms",
    "stream_long_message_delay_ms",
    "stream_send_prefix",
    "stream_send_suffix",
    "stream_send_max_queue",
    "stream_queue_full_policy",
    "stream_flush_on_finish",
    "stream_keep_order",
    "speech_via_tool",
    "speech_max_bubbles",
    "speech_max_bubble_chars",
    "speech_reminder_rounds",
    "speech_force_reply_on_call",
    "speech_fallback_enable",
)


class Module(BaseModule):
    name = "输出策略"
    sign = "LlmOutput"
    description = "流式输出与发言通道：决定一轮回复怎么产出来（一次性 / 逐句流式 / 文本即思考）"
    permission = "everyone"
    subscribe = ()
    category = "LLM"
    order = 10
    #: 接管框架的输出策略槽（禁用/卸载自动还原成"按配置自动选择"）
    provides = ("output",)
    supersedes = ("output",)
    default_config = {
        "mode": "text",
        "stream_output": False,
        "stream_send_pool_enabled": False,
        "stream_send_by_sentence": True,
        "stream_sentence_max_length": 200,
        "stream_send_interval_mode": "none",
        "stream_send_interval_base_ms": 0,
        "stream_send_interval_min_ms": 0,
        "stream_send_interval_max_ms": 0,
        "stream_send_curve": "none",
        "stream_send_curve_k": 1.0,
        "stream_short_message_length": 10,
        "stream_short_message_delay_ms": 300,
        "stream_long_message_delay_ms": 1200,
        "stream_send_prefix": "",
        "stream_send_suffix": "",
        "stream_send_max_queue": 20,
        "stream_queue_full_policy": "backpressure",
        "stream_flush_on_finish": False,
        "stream_keep_order": True,
        "speech_via_tool": False,
        "speech_max_bubbles": 5,
        "speech_max_bubble_chars": 200,
        "speech_reminder_rounds": 2,
        "speech_force_reply_on_call": True,
        "speech_fallback_enable": False,
    }
    config_schema = SCHEMA

    async def on_load(self) -> None:
        """把本模块的输出策略装进运行时（`supersedes` 已建租约，这里给实现）。"""
        runtime = self.ctx.services.runtime(self.bot_id) if self.ctx.services else None
        if runtime is None:
            return
        from app.agent.delivery.output import ConfigView

        # 策略视图：让**其它机制**（如 prompt_prep 的发言通道门控）也能读到这些键
        runtime.output_options = ConfigView(
            getattr(runtime, "config", None), self.config, OWNED_KEYS
        )
        runtime.output = OutputStrategyAdapter(runtime, self.config, OWNED_KEYS)

    async def on_unload(self) -> None:
        """卸载时把槽恢复成"按配置自动选择"（框架默认行为）。

        正常路径由 FeatureRegistry 的租约还原完成；这里兜底处理
        "模块被直接卸载但租约没走完"的极端情况。
        """
        runtime = self.ctx.services.runtime(self.bot_id) if self.ctx.services else None
        if runtime is None:
            return
        if isinstance(getattr(runtime, "output", None), OutputStrategyAdapter):
            runtime.output = None
        runtime.output_options = None

    # ── 供测试/诊断：当前生效的模式 ──
    @property
    def mode(self) -> str:
        raw = str(self.config.get("mode", "text") or "text").strip().lower()
        if raw == "stream" or (raw == "text" and self.config.get("stream_output", False)):
            return "stream"
        if raw == "speech":
            return "speech"
        return "text"


def owned_keys() -> tuple[str, ...]:
    """本模块持有的配置键（供迁移脚本与契约测试核对）。"""
    return OWNED_KEYS


def adapter_for(runtime: Any, config: Any) -> Any:
    """构造本模块的输出策略适配器（便于测试直接调用）。"""
    return OutputStrategyAdapter(runtime, config, OWNED_KEYS)
