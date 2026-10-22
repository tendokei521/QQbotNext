"""主动消息模块的服务层：用框架实现 + 本模块配置组装管理器。"""

from __future__ import annotations

from typing import Any

from app.agent.policy import RuntimeFacade, facade_for


def build_manager(runtime: Any, module_config: Any, owned_keys: Any) -> Any:
    """构造主动消息管理器（`ProactiveManager` 是机制，配置来自本模块）。"""
    from app.agent.proactivity.manager import ProactiveManager

    return ProactiveManager(facade_for(runtime, "proactive", module_config, owned_keys))


__all__ = ["RuntimeFacade", "build_manager"]
