"""流式发送间隔策略：根据配置和消息长度计算每条消息发送前的等待时间。"""

from __future__ import annotations

import math
from typing import Any

from app.domain.message import Message


class SendPolicy:
    def __init__(self, config: dict | None = None) -> None:
        self.config = config or {}

    def next_delay(self, msg: Any) -> float:
        """返回下一条消息发送前需要等待的秒数。"""
        mode = self.config.get("stream_send_interval_mode", "none")
        if mode == "none":
            return 0.0

        text = getattr(msg, "text", "") or str(msg or "")
        length = len(text)
        base = float(self.config.get("stream_send_interval_base_ms", 600) or 600)
        curve = self.config.get("stream_send_curve", "sqrt")
        k = float(self.config.get("stream_send_curve_k", 200) or 200)

        if mode == "fixed":
            delay = base
        else:  # length_curve
            if curve == "sqrt":
                delay = base + k * math.sqrt(length)
            elif curve == "log":
                delay = base + k * math.log1p(length)
            elif curve == "inverse":
                delay = base + k / (1 + length)
            elif curve == "short_long":
                threshold = float(self.config.get("stream_short_message_length", 10) or 10)
                if length <= threshold:
                    delay = float(self.config.get("stream_short_message_delay_ms", 1200) or 1200)
                else:
                    delay = float(self.config.get("stream_long_message_delay_ms", 400) or 400)
            else:
                delay = base + k * math.sqrt(length)

        min_delay = float(self.config.get("stream_send_interval_min_ms", 100) or 100)
        max_delay = float(self.config.get("stream_send_interval_max_ms", 3000) or 3000)
        return max(min_delay, min(max_delay, delay)) / 1000.0
