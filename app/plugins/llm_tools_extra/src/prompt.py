"""工具扩展模块自带的「可用表情」提示段（L3）。

贴表情的能力来自本模块的工具源（`set_msg_emoji_like`）：工具不在，标签表就不该出现。
所以文案由本模块注册成段落，框架只负责排序与合并（`app/agent/assembly/prompt.py`
里的 `emoji_hint_line` 已删）。
"""

from __future__ import annotations

from typing import Any

from app.core.logger import account_label, logger


def emoji_hint_line(tags: str) -> str:
    """【可用表情】一行提示：把可贴标签摆到 prompt 里，省得模型去翻工具文档。"""
    text = str(tags or "").strip()
    if not text:
        return ""
    return (
        f"【可用表情】可贴的表情标签：{text}。"
        "贴表情时直接写标签名（如 赞 / 笑哭 / 吃瓜 / 狗头），框架会翻成正确的表情 id；"
        "若上下文里出现过 [♡66] 这类数字，那是别的消息上已贴过的表情 id，可原样复用。"
    )


def emoji_hint_section(req: Any) -> list[dict]:
    """注入条件：本轮真能贴表情 + 活跃度 > 0 + 词典里有标签（任一不满足整段消失）。"""
    tools = getattr(req, "available_tools", None)
    if tools is not None and "set_msg_emoji_like" not in tools:
        return []
    try:
        from app.agent.persona.emoji import available_tags

        tags = available_tags(20)
    except Exception as e:  # noqa: BLE001 —— 词典不可用不该影响整轮组装
        logger.add_info(account_label(getattr(getattr(req, "runtime", None), "bot_id", "?"))).warning(
            f"[Assembly] 读取表情词典失败（已跳过表情提示）: {e}"
        )
        return []
    text = emoji_hint_line(tags)
    return [{"role": "system", "content": text}] if text else []
