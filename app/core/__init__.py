"""核心内核：settings / container / logger / task_manager / event_bus。"""

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
    module_logger,
    set_console_mode,
    setup_logging,
    task_logger,
    websocket_logger,
    webui_logger,
)
from app.core.settings import Settings, get_settings, load_settings
from app.core.task_manager import TaskManager, get_task_manager

# 注意：这里**不要**再 `from app.core.logger import logger`。
# 包属性 `logger` 会与同名子模块 `app.core.logger` 冲突：一旦在 `__init__` 里绑定
# 同名实例，`import app.core.logger as m` 拿到的就是那个实例而不是模块，
# 导致 `m.xxx` 访问子模块成员时行为诡异（tests/agent/loop/test_pipeline_task_errors.py
# 的 monkeypatch 就踩过）。需要 logger 实例请显式
# `from app.core.logger import logger`，需要模块请 `importlib.import_module("app.core.logger")`。
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
    "api_logger",
    "module_logger",
    "task_logger",
    "webui_logger",
    "websocket_logger",
    "TaskManager",
    "get_task_manager",
]
