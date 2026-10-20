"""防撤回业务入口（service 包拆分：handler/forward/cache/migrate）。

入口链：module.py → service.handle_message(module, event)（消息缓存）
       / service.handle_recall(module, event)（撤回转发）。
"""

from .cache import on_load
from .handler import handle_message, handle_recall
from .migrate import migrate_legacy_config

__all__ = ["handle_message", "handle_recall", "on_load", "migrate_legacy_config"]
