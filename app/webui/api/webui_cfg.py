"""WebUI 自身配置 API（日志展示、单一服务、多群管理）。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.webui.api.deps import get_container

router = APIRouter(tags=["webui"])


def _ok(message: str, **extra):
    return JSONResponse(content={"status": "success", "message": message, **extra})


def _err(status: int, message: str):
    return JSONResponse(status_code=status, content={"status": "error", "message": message})


@router.get("/webui/config")
async def get_webui_config(request: Request):
    from app.infrastructure.config.config_service import ConfigService

    return JSONResponse(content=get_container(request).get(ConfigService).get_webui_config())


@router.post("/webui/config")
async def save_webui_config(request: Request):
    from app.infrastructure.config.config_service import ConfigService

    container = get_container(request)
    cfg_service = container.get(ConfigService)
    data = await request.json()
    current = cfg_service.get_webui_config()
    if "logs" in data:
        current["logs"].update(data["logs"] or {})
    await cfg_service.save_webui_config(current)
    return _ok("配置已保存")


@router.post("/webui/config/logs")
async def save_logs_config(request: Request):
    from app.infrastructure.config.config_service import ConfigService

    container = get_container(request)
    cfg_service = container.get(ConfigService)
    data = await request.json()
    config = cfg_service.get_webui_config()
    logs_cfg = config.setdefault("logs", {})
    for key in ("visible_levels", "max_lines", "console_height"):
        if key in data:
            logs_cfg[key] = data[key]
    await cfg_service.save_webui_config(config)
    return _ok("日志配置已保存")


@router.get("/webui/single-service")
async def get_single_service(request: Request):
    from app.infrastructure.config.config_service import ConfigService

    cfg = get_container(request).get(ConfigService).get_webui_config()
    return JSONResponse(content={"single_service": cfg.get("single_service", {})})


@router.post("/webui/single-service")
async def save_single_service(request: Request):
    from app.infrastructure.config.config_service import ConfigService

    container = get_container(request)
    cfg_service = container.get(ConfigService)
    data = await request.json()
    config = cfg_service.get_webui_config()
    config["single_service"] = data.get("single_service", {})
    await cfg_service.save_webui_config(config)
    return _ok("单一服务配置已保存")


@router.get("/webui/multi-group")
async def get_multi_group(request: Request):
    from app.infrastructure.config.config_service import ConfigService

    cfg = get_container(request).get(ConfigService).get_webui_config()
    return JSONResponse(content={"multi_group": cfg.get("multi_group", {"show_all": False, "groups": {}})})


@router.post("/webui/multi-group")
async def save_multi_group(request: Request):
    from app.infrastructure.config.config_service import ConfigService

    container = get_container(request)
    cfg_service = container.get(ConfigService)
    data = await request.json()
    config = cfg_service.get_webui_config()
    config["multi_group"] = data.get("multi_group", {"show_all": False, "groups": {}})
    await cfg_service.save_webui_config(config)
    return _ok("多群管理配置已保存")
