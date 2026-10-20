"""三角洲行动今日密码业务入口（service 包拆分：handler/fetch/essence/migrate）。

唯一入口链不变：module.py → service.handle(module, event)。
"""

from .handler import daily_push, handle, register_schedule
from .migrate import migrate_legacy_config

__all__ = ["handle", "daily_push", "register_schedule", "migrate_legacy_config"]
