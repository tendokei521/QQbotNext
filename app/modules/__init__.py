"""插件系统：BaseModule / 注册表 / 权限 / 事件分发 / 插件 API。"""

from app.modules.api import get_config_path, get_data_path, get_modules, register_daily_schedule
from app.modules.authority import (
    AUTHORITY_TYPES,
    check_event_permission,
    check_module_enabled,
    check_permission,
    set_system_authority,
)
from app.modules.base import (
    BaseModule,
    ModuleAuthority,
    ModuleConfig,
    ModuleContext,
    ModulePermission,
    ServiceAccess,
    resolve_enabled_ids,
)
from app.modules.dispatcher import ModuleDispatcher
from app.modules.keyword import match_keywords
from app.modules.registry import ModuleRegistry

__all__ = [
    "BaseModule",
    "ModuleAuthority",
    "ModuleConfig",
    "ModuleContext",
    "ModulePermission",
    "ServiceAccess",
    "ModuleRegistry",
    "ModuleDispatcher",
    "AUTHORITY_TYPES",
    "check_module_enabled",
    "check_event_permission",
    "check_permission",
    "set_system_authority",
    "resolve_enabled_ids",
    "match_keywords",
    # 插件 API
    "get_modules",
    "get_config_path",
    "get_data_path",
    "register_daily_schedule",
]
