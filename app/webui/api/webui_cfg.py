"""WebUI 自身配置 API（日志展示、单一服务、多群管理）。

依赖 `ConfigService` 一律通过 `Depends(get_config_service)` 注入——读一个路由就能看出
它依赖谁，不必追到装配根。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.webui.api.deps import get_config_service

router = APIRouter(tags=["webui"])


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
async def save_single_service(request: Request, cfg_service=Depends(get_config_service)):
    try:
        data = await request.json()
    except Exception:
        data = {}
    config = cfg_service.get_webui_config()
    config["single_service"] = data.get("single_service", {})
    await cfg_service.save_webui_config(config)
    return _ok("单一服务配置已保存")


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
