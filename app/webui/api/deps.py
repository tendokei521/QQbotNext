"""API 公共依赖：容器与各服务的 FastAPI 注入入口。

## 为什么需要这一层

路由函数应当只声明「我需要哪个服务」，而不是自己 `container.get(类型)`——
后者是服务定位：调用方被焊死在容器 API 上，读代码时要一路追到装配根才知道
这个端点到底依赖谁。

这里给每个常用服务一个具名依赖，路由里写：

    async def api_xxx(bot_service: BotService = Depends(get_bot_service)):
        ...

好处：**依赖在签名上就看得见**，测试可以直接传假对象，不必造容器。

## 容器本身

`get_container` 仍然保留——有些端点要拿装配期才算得出的东西（例如按 bot 取
runtime 的组合）。新代码优先用具名服务依赖。
"""

from __future__ import annotations

from fastapi import Query, Request


def get_container(request: Request):
    """当前请求的应用容器（`app.state.container`，由 `create_app` 注入）。"""
    return request.app.state.container


def get_service(request: Request, service_type):
    """按类型解析服务。优先用下面那些具名依赖——它们能让依赖关系出现在签名里。"""
    return request.app.state.container.get(service_type)


def parse_bot_id(bot_id: str | None = Query(None)) -> int | None:
    """把查询参数 bot_id 安全转为 int；前端可能传 null/'null'/'None'。"""
    if not bot_id or bot_id in ("null", "None", ""):
        return None
    try:
        return int(bot_id)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# 具名服务依赖（每个都从容器解析同一类型；名字让签名自解释）
# --------------------------------------------------------------------------- #
def get_bot_service(request: Request):
    from app.services.bot_service import BotService

    return get_service(request, BotService)


def get_config_service(request: Request):
    from app.infrastructure.config.config_service import ConfigService

    return get_service(request, ConfigService)


def get_log_service(request: Request):
    from app.services.log_service import LogService

    return get_service(request, LogService)


def get_module_registry(request: Request):
    from app.plugin.registry import ModuleRegistry

    return get_service(request, ModuleRegistry)


def get_feature_registry(request: Request):
    from app.plugin.features import FeatureRegistry

    return get_service(request, FeatureRegistry)


def get_resource_registry(request: Request):
    from app.services.resource_registry import ResourceRegistry

    return get_service(request, ResourceRegistry)


def get_provider_registry(request: Request):
    from app.services.provider_service import ProviderRegistry

    return get_service(request, ProviderRegistry)


def get_provider_preset_service(request: Request):
    from app.services.provider_preset_service import ProviderPresetService

    return get_service(request, ProviderPresetService)


def get_provider_model_service(request: Request):
    from app.services.provider_model_service import ProviderModelService

    return get_service(request, ProviderModelService)


def get_config_profile_service(request: Request):
    from app.services.config_profile_service import ConfigProfileService

    return get_service(request, ConfigProfileService)


def get_role_preset_service(request: Request):
    from app.services.role_preset_service import RolePresetService

    return get_service(request, RolePresetService)


def get_module_install_service(request: Request):
    from app.services.module_install_service import ModuleInstallService

    return get_service(request, ModuleInstallService)


def get_session_history_service(request: Request):
    from app.services.session_history_service import SessionHistoryService

    return get_service(request, SessionHistoryService)


def get_settings(request: Request):
    from app.core.settings import Settings

    return get_service(request, Settings)


def get_service_access(request: Request):
    from app.plugin.base import ServiceAccess

    return get_service(request, ServiceAccess)


def get_gateway(request: Request):
    from app.infrastructure.onebot.gateway import OneBotGateway

    return get_service(request, OneBotGateway)
