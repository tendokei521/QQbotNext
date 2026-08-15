"""三角洲行动今日密码业务入口（service 包拆分：handler/fetch/essence/migrate）。

唯一入口链不变：module.py → service.handle(module, event)。
"""

from .handler import daily_push, handle
from .migrate import migrate_legacy_config
from .handler import register_schedule

__all__ = ["handle", "daily_push", "register_schedule", "migrate_legacy_config"]
