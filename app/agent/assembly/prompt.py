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

#: user 侧消息元信息格式：``single_line``（默认，"时间 昵称(QQ): 正文"）| ``legacy``（六行块）
USER_MESSAGE_FORMAT_SINGLE = "single_line"
USER_MESSAGE_FORMAT_LEGACY = "legacy"

#: 时间行位置：``tail``（默认，稳定内容靠前，利于前缀缓存）| ``head``
TIME_POSITION_TAIL = "tail"
TIME_POSITION_HEAD = "head"


def user_message_format(config) -> str:
    """本轮 user 侧格式：``single_line``（默认）| ``legacy``（六行元信息块）。"""
    value = _cfg_str(config, "user_message_format", USER_MESSAGE_FORMAT_SINGLE)
    return USER_MESSAGE_FORMAT_LEGACY if value == USER_MESSAGE_FORMAT_LEGACY else USER_MESSAGE_FORMAT_SINGLE


def prompt_time_position(config) -> str:
    """时间行位置：``tail``（默认，挪到末尾让稳定行靠前）| ``head``。"""
    value = _cfg_str(config, "prompt_time_position", TIME_POSITION_TAIL)
    return TIME_POSITION_HEAD if value == TIME_POSITION_HEAD else TIME_POSITION_TAIL


#: Q2：`history_mode=recent` 时插在历史块前面的固定提示。
#: 文案**不随条数变化**——它每轮都会出现，变数字等于每轮改写 system 前缀。
HISTORY_OMITTED_HINT = (
    "（更早的对话没有全部列出。如果用户提到很久以前说过的内容，"
    "用 search_history 工具按关键词检索本会话历史；需要细节再用 expand_message 展开。）"
)
