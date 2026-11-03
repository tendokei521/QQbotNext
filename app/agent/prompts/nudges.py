"""重试类 user 提醒（框架在异常/截断情况下追加的提示）。"""

from __future__ import annotations

REASONING_OVERFLOW_NUDGE = (
    "【系统提醒】上一轮你的思考把输出预算用满了，正文还没开始就被截断，"
    "对方什么都没收到。这一轮不要复述思考过程，直接给最终回答；"
    "如果要看图，就只说你看到的关键内容，别长篇推理。"
)

__all__ = ["REASONING_OVERFLOW_NUDGE"]
