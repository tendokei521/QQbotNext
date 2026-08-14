"""群打卡业务入口（service 包拆分：handler/signin/permission/notify/migrate）。

唯一入口链不变：module.py → service.handle(module, event)。
"""

from .handler import handle
from .migrate import migrate_legacy_config
from .signin import daily_sign_in, register_schedule

__all__ = ["handle", "daily_sign_in", "register_schedule", "migrate_legacy_config"]
