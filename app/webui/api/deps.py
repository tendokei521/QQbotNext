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


def parse_account_id(
    account_id: str | None = Query(None),
    bot_id: str | None = Query(None),
) -> str | None:
    """取「当前操作的账号 id」。

    **语义变更**：历史实现这里返回数组下标 ``int``，参数名 ``bot_id`` 实际指 QQ 号。
    自 v4 起账号唯一凭据是用户在「添加账号」时设定的字符串 id，因此：

    - 首选 ``?account_id=<id>``；``?bot_id=`` 仍被接受（老前端/脚本兼容期）；
    - 返回 ``str | None``：调用方拿它当模块配置 / Agent 运行时 / 会话 / 统计的 key。
    """
    for raw in (account_id, bot_id):
        value = (raw or "").strip()
        if value and value not in ("null", "None"):
            return value
    return None


#: 兼容旧导入名（大量路由仍写 `Depends(parse_account_id)`；语义已是账号 id）。
parse_bot_id = parse_account_id


# --------------------------------------------------------------------------- #
# 具名服务依赖（每个都从容器解析同一类型；名字让签名自解释）
# --------------------------------------------------------------------------- #
def get_bot_service(request: Request):
    from app.application.bot_service import BotService

    return get_service(request, BotService)


def get_config_service(request: Request):
    from app.infrastructure.config.config_service import ConfigService

    return get_service(request, ConfigService)


def get_log_service(request: Request):
    from app.application.log_service import LogService

    return get_service(request, LogService)


def get_module_registry(request: Request):
    from app.plugin.registry import ModuleRegistry

    return get_service(request, ModuleRegistry)


def get_feature_registry(request: Request):
    from app.plugin.features import FeatureRegistry

    return get_service(request, FeatureRegistry)


def get_resource_registry(request: Request):
    from app.application.resource_registry import ResourceRegistry

    return get_service(request, ResourceRegistry)


def get_provider_registry(request: Request):
    from app.application.provider_service import ProviderRegistry

    return get_service(request, ProviderRegistry)


def get_provider_preset_service(request: Request):
    from app.application.provider_preset_service import ProviderPresetService

    return get_service(request, ProviderPresetService)


def get_provider_model_service(request: Request):
    from app.application.provider_model_service import ProviderModelService

    return get_service(request, ProviderModelService)


def get_role_preset_service(request: Request):
    from app.application.role_preset_service import RolePresetService

    return get_service(request, RolePresetService)


def get_module_install_service(request: Request):
    from app.application.module_install_service import ModuleInstallService

    return get_service(request, ModuleInstallService)


def get_session_history_service(request: Request):
    from app.application.session_history_service import SessionHistoryService

    return get_service(request, SessionHistoryService)


def get_agent_manager(request: Request):
    from app.agent.runtime import AgentManager

    return get_service(request, AgentManager)


def get_settings(request: Request):
    from app.core.settings import Settings

    return get_service(request, Settings)


def get_service_access(request: Request):
    from app.plugin.base import ServiceAccess

    return get_service(request, ServiceAccess)


def get_gateway(request: Request):
    from app.infrastructure.onebot.gateway import OneBotGateway

    return get_service(request, OneBotGateway)


def get_responder_service(request: Request):
    from app.application.responder_service import ResponderService

    return get_service(request, ResponderService)
