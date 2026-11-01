"""参与度回复模式（`reply_mode=participation`）：观察 / 决策 / 发送三段。

入口约定见 `docs/plan/participation-reply-mode.md`：

- `observer`：全量观察——把群消息无条件写进会话历史；
- `rules`：唯一的规则来源——成本闸门、冷却、预算、提示词文案都由它派生；
- 发送仍由输出策略（发言通道）负责，"没有调用 send_message" 就是沉默。

注意：档位之间的语义差异**写在系统提示词里**（`participation.LEVEL_PROMPT`），
本地只做成本闸门，不做"该不该说话"的语义判断。
"""

from __future__ import annotations

from app.agent.reply.observer import group_session_id, observe_group_message

__all__ = ["group_session_id", "observe_group_message"]
