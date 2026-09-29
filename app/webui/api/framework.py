"""框架能力清单 API：让前端与插件知道「这个框架有什么」。

``GET /api/framework/capabilities`` 返回可引用资源种类、可用服务、可接管能力、
schema 版本与字段类型清单 —— 模块可据此自检并给出可读的降级提示。
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.modules.base import ServiceAccess
from app.modules.features import FeatureRegistry
from app.services.capabilities import collect_capabilities
from app.services.resource_registry import ResourceRegistry
from app.webui.api.deps import get_container

router = APIRouter(prefix="/framework", tags=["framework"])


@router.get("/capabilities")
async def framework_capabilities(request: Request):
    container = get_container(request)
    capabilities = collect_capabilities(
        services=container.get(ServiceAccess),
        features=container.get(FeatureRegistry),
        resources=container.get(ResourceRegistry),
    )
    return JSONResponse(content={"ok": True, "capabilities": capabilities})
