"""框架静态提示词：以常量/段落数组为主，函数只负责插入运行时值。"""

from __future__ import annotations

# ==================== 身份块（L1 尾部，紧挨在「自身账号」前） ====================

#: 私聊/群聊通用的三行身份事实（`str.format` 注入账号与名册）
ACCOUNT_LINE = "你的账号：{}"
OWNER_LINE = "你的拥有者：{}"
MANAGER_LINE = "你的管理员：{}"

#: 群聊时的首行（inner 已由调用方拼好 group_id/group_name 字段）
GROUP_LINE_TEMPLATE = "【当前群聊[{}]】"
