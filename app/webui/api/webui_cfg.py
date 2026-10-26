"""WebUI 自身配置 API（日志展示、单一服务、多群管理）。

依赖 `ConfigService` 一律通过 `Depends(get_config_service)` 注入——读一个路由就能看出
它依赖谁，不必追到装配根。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.plugin.authority import declares_single_service
from app.webui.api.deps import get_config_service, get_module_registry
from app.webui.api.modules import VIRTUAL_AGENT_MODULE

router = APIRouter(tags=["webui"])


class _VirtualAgentDeclaration:
    """框架级虚拟模块（`agent`）的能力声明。

    它没有真实 `BaseModule` 实例（走 `_AgentProxy`/`_AgentGate` 代理到运行时），
    但它恒支持单一服务，故用一个最小声明体回答「是否声明支持」。
    """

    module_name = VIRTUAL_AGENT_MODULE
    supports_single_service = True


_VIRTUAL_MODULES = {VIRTUAL_AGENT_MODULE: _VirtualAgentDeclaration()}


def _resolve_declaration(registry, module_name: str):
    """取能回答「是否声明支持单一服务」的对象；取不到（模块未加载）返回 None。"""
    virtual = _VIRTUAL_MODULES.get(module_name)
    if virtual is not None:
        return virtual
    if registry is None:
        return None
    try:
        return registry.get(module_name)
    except Exception:  # noqa: BLE001 —— 配置写入不该因为注册表异常而失败
        return None


def _ok(message: str, **extra):
    return JSONResponse(content={"status": "success", "message": message, **extra})


def _err(status: int, message: str):
    return JSONResponse(status_code=status, content={"status": "error", "message": message})


@router.get("/webui/config")
async def get_webui_config(cfg_service=Depends(get_config_service)):
    return JSONResponse(content=cfg_service.get_webui_config())


@router.post("/webui/config")
async def save_webui_config(request: Request, cfg_service=Depends(get_config_service)):
    try:
        data = await request.json()
    except Exception:
        data = {}
    current = cfg_service.get_webui_config()
    if "logs" in data:
        current["logs"].update(data["logs"] or {})
    if "module_preferences" in data:
        current["module_preferences"] = data["module_preferences"] or {}
    if "experimental" in data:
        current.setdefault("experimental", {"show_experimental": False}).update(data["experimental"] or {})
    await cfg_service.save_webui_config(current)
    return _ok("配置已保存")


@router.post("/webui/config/logs")
async def save_logs_config(request: Request, cfg_service=Depends(get_config_service)):
    try:
        data = await request.json()
    except Exception:
        data = {}
    config = cfg_service.get_webui_config()
    logs_cfg = config.setdefault("logs", {})
    for key in ("show_raw_logs", "visible_levels", "max_lines", "console_height"):
        if key in data:
            logs_cfg[key] = data[key]
    await cfg_service.save_webui_config(config)
    return _ok("日志配置已保存")


@router.get("/webui/config/experimental")
async def get_experimental_config(cfg_service=Depends(get_config_service)):
    cfg = cfg_service.get_webui_config()
    return JSONResponse(content=cfg.get("experimental", {"show_experimental": False}))


@router.post("/webui/config/experimental")
async def save_experimental_config(request: Request, cfg_service=Depends(get_config_service)):
    try:
        data = await request.json()
    except Exception:
        data = {}
    config = cfg_service.get_webui_config()
    current = config.setdefault("experimental", {"show_experimental": False})
    if "show_experimental" in data:
        current["show_experimental"] = bool(data["show_experimental"])
    await cfg_service.save_webui_config(config)
    return _ok("实验性选项设置已保存")


@router.get("/webui/module-preferences")
async def get_module_preferences(cfg_service=Depends(get_config_service)):
    cfg = cfg_service.get_webui_config()
    return JSONResponse(content=cfg.get("module_preferences", {}))


@router.post("/webui/module-preferences")
async def save_module_preferences(request: Request, cfg_service=Depends(get_config_service)):
    try:
        data = await request.json()
    except Exception:
        data = {}
    config = cfg_service.get_webui_config()
    config["module_preferences"] = data.get("module_preferences", {}) or {}
    await cfg_service.save_webui_config(config)
    return _ok("模块偏好已保存")


def _legacy_multi_group_policies(config: dict, capability_id: str) -> list[dict]:
    """从旧 ``multi_group.groups[*].service_bot_id`` 生成 delegate 策略（兼容期）。"""
    groups = ((config or {}).get("multi_group") or {}).get("groups") or {}
    policies: list[dict] = []
    for channel_id, group_config in groups.items():
        primary = (group_config or {}).get("service_bot_id")
        if primary in (None, ""):
            continue
        policies.append({
            "group_id": str(channel_id),
            "policy": "delegate",
            "primary_account_id": str(primary),
            "fallback": "open",
        })
    return policies


def _declared_modules(config_service) -> set[str]:
    """旧配置里开启过单一服务的模块（兼容期标记，真实策略在 channel_policies）。"""
    try:
        single = (config_service.get_webui_config().get("single_service") or {})
    except Exception:  # noqa: BLE001
        return set()
    return {str(module) for module, enabled in single.items() if enabled}


@router.get("/webui/single-service")
async def get_single_service(cfg_service=Depends(get_config_service)):
    """已废弃：请用 ``/api/modules/{module}/responder``。返回值由 channel_policies 派生。"""
    capabilities = {row["capability_id"] for row in cfg_service.list_channel_policies()}
    return JSONResponse(content={
        "single_service": {cap: True for cap in sorted(capabilities)},
        "deprecated": True,
    })


@router.post("/webui/single-service")
async def save_single_service(
    request: Request,
    cfg_service=Depends(get_config_service),
    registry=Depends(get_module_registry),
):
    """已废弃的兼容入口：把旧「单一服务开关」翻译成 channel_policies。

    - 打开：按当前「多群管理」里的服务账号生成 delegate 策略（旧 UI 的两步流程仍可用）；
    - 关闭：清空该模块的群策略（回到 all）；
    - 仍写入 ``webui.single_service`` 只作为**兼容标记**，运行时不再读它。

    新代码请用 ``POST /api/modules/{module}/responder``。
    """
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        data = {}
    incoming = (data or {}).get("single_service", {}) or {}

    current = cfg_service.get_webui_config()
    legacy_marker = dict(current.get("single_service") or {})
    accepted: dict[str, bool] = {}
    rejected: list[str] = []
    for module_name, enabled in incoming.items():
        declaration = _resolve_declaration(registry, module_name)
        if declaration is None or declares_single_service(declaration):
            accepted[str(module_name)] = bool(enabled)
        else:
            rejected.append(str(module_name))
            continue
        if enabled:
            legacy_marker[str(module_name)] = True
            policies = _legacy_multi_group_policies(current, str(module_name))
        else:
            legacy_marker.pop(str(module_name), None)
            policies = []
        await cfg_service.save_channel_policies(str(module_name), policies)

    current["single_service"] = legacy_marker
    await cfg_service.save_webui_config(current)
    return _ok(
        "单一服务配置已保存（兼容入口，等价于群响应权策略）",
        rejected=rejected,
        deprecated=True,
    )


@router.get("/webui/multi-group")
async def get_multi_group(cfg_service=Depends(get_config_service)):
    cfg = cfg_service.get_webui_config()
    return JSONResponse(content={"multi_group": cfg.get("multi_group", {"show_all": False, "groups": {}})})


@router.post("/webui/multi-group")
async def save_multi_group(request: Request, cfg_service=Depends(get_config_service)):
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        data = {}
    config = cfg_service.get_webui_config()
    config["multi_group"] = data.get("multi_group", {"show_all": False, "groups": {}})
    await cfg_service.save_webui_config(config)
    # 兼容期：旧 UI 通过这里修改服务账号；只同步「仍用旧开关」的模块，避免覆盖新策略
    for capability_id in _declared_modules(cfg_service):
        await cfg_service.save_channel_policies(
            capability_id, _legacy_multi_group_policies(config, capability_id)
        )
    return _ok("多群管理配置已保存")
