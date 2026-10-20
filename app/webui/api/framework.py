"""框架能力清单 API：让前端与插件知道「这个框架有什么」。

``GET /api/framework/capabilities`` 返回可引用资源种类、可用服务、可接管能力、
schema 版本与字段类型清单 —— 模块可据此自检并给出可读的降级提示。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.application.capabilities import collect_capabilities
from app.webui.api.deps import get_feature_registry, get_resource_registry, get_service_access

router = APIRouter(prefix="/framework", tags=["framework"])


@router.get("/capabilities")
async def framework_capabilities(
    services=Depends(get_service_access),
    features=Depends(get_feature_registry),
    resources=Depends(get_resource_registry),
):
    capabilities = collect_capabilities(
        services=services,
        features=features,
        resources=resources,
    )
    return JSONResponse(content={"ok": True, "capabilities": capabilities})
