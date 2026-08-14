"""日志 API。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.webui.api.deps import get_container

router = APIRouter(tags=["logs"])


@router.get("/logs")
async def api_logs(request: Request):
    from app.infrastructure.config.config_service import ConfigService
    from app.services.log_service import LogService

    container = get_container(request)
    config = container.get(ConfigService).get_webui_config()
    levels = config.get("logs", {}).get("visible_levels", ["info", "warning", "error"])
    max_lines = config.get("logs", {}).get("max_lines", 50)
    return JSONResponse(content=container.get(LogService).get_recent_logs(max_lines, levels))
