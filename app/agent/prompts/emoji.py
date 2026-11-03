"""【可用表情】提示：把可贴标签摆到 prompt 里，省得模型去翻工具文档。"""

from __future__ import annotations

EMOJI_HINT_TEMPLATE = (
    "【可用表情】可贴的表情标签：{}。"
    "贴表情时直接写标签名（如 赞 / 笑哭 / 吃瓜 / 狗头），框架会翻成正确的表情 id；"
    "若上下文里出现过 [♡66] 这类数字，那是别的消息上已贴过的表情 id，可原样复用。"
)


def emoji_hint_line(tags: str) -> str:
    """把表情标签清单渲染成一行提示；空清单返回空串。"""
    text = str(tags or "").strip()
    return EMOJI_HINT_TEMPLATE.format(text) if text else ""
