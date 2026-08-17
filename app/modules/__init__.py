"""插件系统：BaseModule / 注册表 / 权限 / 事件分发 / 插件 API。"""

from app.modules.api import get_config_path, get_data_path, get_modules, register_daily_schedule
from app.modules.authority import (
    PERMISSIONS,
    check_module_enabled,
    check_module_permission,
    compute_event_permission,
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
from app.modules.hooks import SendContext, SendHookRegistry, llm_hook, module_hook, send_hook
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
    "PERMISSIONS",
    "check_module_enabled",
    "check_module_permission",
    "compute_event_permission",
    "resolve_enabled_ids",
    "match_keywords",
    "module_hook",
    "llm_hook",
    "send_hook",
    "SendContext",
    "SendHookRegistry",
    # 插件 API
    "get_modules",
    "get_config_path",
    "get_data_path",
    "register_daily_schedule",
]
