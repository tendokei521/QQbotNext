"""Prompt 构建（纯函数，化用 AstrBot 上下文组装思想）。

把「系统提示 + 工具协议 + 前置历史 + 会话历史 + 用户消息」组装成 LLM 消息列表，
与 Provider 调用解耦，便于单测与复用。
"""

from __future__ import annotations


def _cfg_str(config, key: str, default: str) -> str:
    """从配置读取字符串项（缺省/异常/空串时返回 default，统一小写去空白）。"""
    value = default
    if config is not None and hasattr(config, "get"):
        try:
            value = config.get(key, default)
        except Exception:
            value = default
    text = str(value if value is not None else default).strip().lower()
    return text or default


# ==================== 用户侧格式 ====================
#
# 这里曾经有一对「组装档位」常量（``legacy`` / ``qq_agent``），用一道总闸决定
# "要不要注入行为规则段 / 消息格式走单行还是多行 / 时间戳放头还是尾"。
# 它带来的默认状态是"新功能全关"：选了提示词方案也拿不到行为段（详见
# docs/reviews/2026-10-31-prompt-assembly-analysis.md）。
# 现在档位没了，每个键就是它自己那个语义，默认值直接就是产品想要的形态。

#: user 侧消息元信息格式：``single_line``（默认，"昵称(QQ): 正文"）| ``multi_line``（六行块）
USER_MESSAGE_FORMAT_SINGLE = "single_line"
USER_MESSAGE_FORMAT_MULTI = "multi_line"
#: 兼容旧值（历史配置里出现过 ``legacy``）
USER_MESSAGE_FORMAT_LEGACY = "legacy"

#: 时间行位置：``tail``（默认，稳定内容靠前）| ``head`` | ``off``（不注入）
TIME_POSITION_TAIL = "tail"
TIME_POSITION_HEAD = "head"
TIME_POSITION_OFF = "off"


def user_message_format(config) -> str:
    """本轮 user 侧格式：``single_line``（默认）| ``multi_line``（六行元信息块）。"""
    value = _cfg_str(config, "user_message_format", USER_MESSAGE_FORMAT_SINGLE)
    if value in (USER_MESSAGE_FORMAT_MULTI, USER_MESSAGE_FORMAT_LEGACY):
        return USER_MESSAGE_FORMAT_MULTI
    return USER_MESSAGE_FORMAT_SINGLE


def prompt_time_position(config) -> str:
    """时间行位置：``tail``（默认）| ``head`` | ``off``。"""
    value = _cfg_str(config, "prompt_time_position", TIME_POSITION_TAIL)
    if value == TIME_POSITION_OFF:
        return TIME_POSITION_OFF
    return TIME_POSITION_HEAD if value == TIME_POSITION_HEAD else TIME_POSITION_TAIL


# Q2 的历史省略提示已迁到 ``app/agent/prompts/history.py``（HISTORY_OMITTED_HINT）。
