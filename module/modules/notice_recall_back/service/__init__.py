"""防撤回业务入口（service 包拆分：handler/forward/cache/migrate）。

唯一入口链不变：module.py → service.handle(module, event)。
"""

from .cache import on_load
from .handler import handle
from .migrate import migrate_legacy_config

__all__ = ["handle", "on_load", "migrate_legacy_config"]
