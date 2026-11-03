"""历史相关提示：省略提示（history_mode=recent）与压缩摘要头。"""

from __future__ import annotations

#: Q2：`history_mode=recent` 时插在历史块前面的固定提示。
#: 文案**不随条数变化**——它每轮都会出现，变数字等于每轮改写 system 前缀。
HISTORY_OMITTED_HINT = "（更早的对话没有全部列出。）"

#: 压缩后替换旧历史时，摘要块的标题
SUMMARY_HEADER = "【更早对话摘要】\n"

__all__ = ["HISTORY_OMITTED_HINT", "SUMMARY_HEADER"]
