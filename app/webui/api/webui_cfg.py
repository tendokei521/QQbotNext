"""WebUI 自身配置 API（日志展示、单一服务、多群管理）。

依赖 `ConfigService` 一律通过 `Depends(get_config_service)` 注入——读一个路由就能看出
它依赖谁，不必追到装配根。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.agent.config import AGENT_CONFIG_MODULE
from app.plugin.authority import declares_single_service
from app.webui.api.deps import get_config_service, get_module_registry

router = APIRouter(tags=["webui"])


class _VirtualAgentDeclaration:
    """框架级虚拟模块（`agent`）的能力声明。

    它没有真实 `BaseModule` 实例（走 `_AgentProxy`/`_AgentGate` 代理到运行时），
    但它恒支持单一服务，故用一个最小声明体回答「是否声明支持」。
    """

    module_name = AGENT_CONFIG_MODULE
    supports_single_service = True


_VIRTUAL_MODULES = {AGENT_CONFIG_MODULE: _VirtualAgentDeclaration()}


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


@router.get("/webui/single-service")
async def get_single_service(cfg_service=Depends(get_config_service)):
    cfg = cfg_service.get_webui_config()
    return JSONResponse(content={"single_service": cfg.get("single_service", {})})


@router.post("/webui/single-service")
async def save_single_service(
    request: Request,
    cfg_service=Depends(get_config_service),
    registry=Depends(get_module_registry),
):
    """保存单一服务开关（整值替换）。

    单一服务是**声明制**能力：只有 `BaseModule.supports_single_service = True` 的模块
    才接受写入，其余模块被丢弃并在 `rejected` 里回报（前端据此提示）。已加载但未声明
    的模块若历史配置里残留 true，会在本次保存时被清掉；未加载（注册表里查不到）的模块
    原样保留，避免热重载/加载失败时误删配置。
    """
    try:
        data = await request.json()
    except Exception:
        data = {}
    incoming = (data or {}).get("single_service", {}) or {}

    config = cfg_service.get_webui_config()
    accepted: dict[str, bool] = {}
    rejected: list[str] = []
    for module_name, enabled in incoming.items():
        declaration = _resolve_declaration(registry, module_name)
        if declaration is None or declares_single_service(declaration):
            accepted[str(module_name)] = bool(enabled)
        else:
            rejected.append(str(module_name))

    config["single_service"] = accepted
    await cfg_service.save_webui_config(config)
    return _ok("单一服务配置已保存", rejected=rejected)


@router.get("/webui/multi-group")
async def get_multi_group(cfg_service=Depends(get_config_service)):
    cfg = cfg_service.get_webui_config()
    return JSONResponse(content={"multi_group": cfg.get("multi_group", {"show_all": False, "groups": {}})})


@router.post("/webui/multi-group")
async def save_multi_group(request: Request, cfg_service=Depends(get_config_service)):
    try:
        data = await request.json()
    except Exception:
        data = {}
    config = cfg_service.get_webui_config()
    config["multi_group"] = data.get("multi_group", {"show_all": False, "groups": {}})
    await cfg_service.save_webui_config(config)
    return _ok("多群管理配置已保存")
