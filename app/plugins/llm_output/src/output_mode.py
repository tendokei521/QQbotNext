"""输出模块的配置视图。

历史 `mode`（text / stream / speech 三选一）已删除：它把两个正交维度混成一个开关，
还导致"发言通道必须走流式"。现在：

- **通道**（文本即回复 / 发言通道）= 由回复模式决定（框架）；
- **传输**（流式 / 一次拿全）= 由 `stream_output` 决定（框架读本视图）。

本模块只提供 `stream_*` / `speech_max_*` 这些**实现参数**，不再判定形态。
"""

from __future__ import annotations

from app.agent.delivery.output import ConfigView


class OutputOptionsView(ConfigView):
    """给装配/提示词层读的策略视图（挂在 ``runtime.output_options``）。"""


__all__ = ["OutputOptionsView"]
