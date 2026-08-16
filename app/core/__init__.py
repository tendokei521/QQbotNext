"""核心内核：settings / container / logger / task_manager / event_bus。"""

from app.core import logger
from app.core.container import Container
from app.core.event_bus import (
    BotLifecycleEvent,
    ConfigChangedEvent,
    EventBus,
    ModulesReloadedEvent,
    event_bus,
)
from app.core.logger import (
    api_logger,
    get_logger,
    get_module_logger,
    logger,
    module_logger,
    set_console_mode,
    setup_logging,
    task_logger,
    webui_logger,
    websocket_logger,
)
from app.core.settings import Settings, get_settings, load_settings
from app.core.task_manager import TaskManager, get_task_manager

__all__ = [
    "Container",
    "EventBus",
    "event_bus",
    "ConfigChangedEvent",
    "BotLifecycleEvent",
    "ModulesReloadedEvent",
    "Settings",
    "get_settings",
    "load_settings",
    "setup_logging",
    "set_console_mode",
    "get_logger",
    "get_module_logger",
    "logger",
    "api_logger",
    "module_logger",
    "task_logger",
    "webui_logger",
    "websocket_logger",
    "TaskManager",
    "get_task_manager",
]
