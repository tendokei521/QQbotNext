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
    # 发言通道本身（是否走"文本=思考"）归回复模式决定，本模块不再持有该开关；
    # 这里保留的 speech_* 只是发言通道启用后的**气泡约束**。
    "speech_max_bubbles",
    "speech_max_bubble_chars",
)


class Module(BaseModule):
    name = "流式输出"
    sign = "LlmOutput"
    description = "决定一轮回复怎么发：一次发完、逐句发还是先想再说"
    permission = "everyone"
    subscribe = ()
    category = "LLM"
    order = 10
    #: 接管框架的输出策略槽（禁用/卸载自动还原成"按配置自动选择"）
    provides = ("output",)
    supersedes = ("output",)
    default_config = {
        # 传输方式只有一个开关：`stream_output`（流式 / 一次拿全）。
        # 通道（文本即回复 / 发言通道）归回复模式，不在本模块。
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
        "speech_max_bubbles": 5,
        "speech_max_bubble_chars": 200,
    }
    config_schema = SCHEMA

    async def on_load(self) -> None:
        """把本模块的输出策略装进运行时（`supersedes` 已建租约，这里给实现）。"""
        runtime = self.ctx.services.runtime(self.bot_id) if self.ctx.services else None
        if runtime is None:
            return

        adapter = OutputStrategyAdapter(runtime, self.config, OWNED_KEYS)
        # 策略视图：让装配/提示词层读到与本模块同一口径的 `stream_*` 参数。
        # 通道（发言通道与否）由回复模式决定，不在本模块；传输只有 `stream_output` 一个键。
        runtime.output_options = adapter.options_view()
        runtime.output = adapter
        # 不再声明输出能力：策略选择统一由框架 `select_default_output` 完成
        # （通道=回复模式 × 传输=stream_output）。模块声明会把"通道"重新抢回去。

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

    # ── 供测试/诊断：当前生效的传输方式 ──
    @property
    def transport(self) -> str:
        """``stream`` / ``blocking``（通道不在这里判定，见框架 select_default_output）。"""
        try:
            return "stream" if bool(self.config.get("stream_output", False)) else "blocking"
        except Exception:  # noqa: BLE001 —— 配置坏了按最保守的传输方式
            return "blocking"


def owned_keys() -> tuple[str, ...]:
    """本模块持有的配置键（供迁移脚本与契约测试核对）。"""
    return OWNED_KEYS


def adapter_for(runtime: Any, config: Any) -> Any:
    """构造本模块的输出策略适配器（便于测试直接调用）。"""
    return OutputStrategyAdapter(runtime, config, OWNED_KEYS)
