"""工具结果末尾的「回应要求」（由 `loop/tool_loop.append_reply_directive` 注入）。"""

from __future__ import annotations

#: 默认「回应要求」：只讲"用什么口吻回应"，不涉及具体内容
DEFAULT_REPLY_DIRECTIVE = (
    "（上面这些是给你自己看的资料，不是要你转述的稿子：直接用你自己的口吻回应用户，"
    "不要复述、不要列条目、不要以“根据记录/已展开”开头；一两句就够。"
    "若还需要更多信息，可以继续调用工具。）"
)

__all__ = ["DEFAULT_REPLY_DIRECTIVE"]
