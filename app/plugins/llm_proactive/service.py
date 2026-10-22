"""llm_proactive 的服务层：主动消息 = 订阅事实 + 用基础接口干活。

框架在 R0 之后只广播三件事：`message_observed` / `bot_sent` / `turn_completed`。
本模块订阅前两个，用 `spawn` 起计时器、`llm_turn` 跑一轮、`send_to` 发出去。
"""

from __future__ import annotations

from typing import Any


def build_manager(module: Any) -> Any:
    """构造主动消息管理器。

    `module` 就是**插件模块实例**（`BaseModule`）：它有 `config`（本模块配置）、
    `bot_id`、`llm_turn` / `send_to` / `spawn` 等基础接口，以及 `ctx.services`。
    """
    from app.plugins.llm_proactive.manager import ProactiveManager

    return ProactiveManager(module)
