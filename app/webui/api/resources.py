"""系统资源候选 API（供模块配置里 `type: "resource"` 的下拉框与诊断用）。

- ``GET /api/resources`` → 已注册的资源种类
- ``GET /api/resources/{kind}?bot_id=&<filters>`` → ``[{"value","label"}]``

未注册的 kind 返回 404 而不是空数组：前端据此显示"资源种类未注册"，
避免写成"空下拉"让用户以为没有数据。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.webui.api.deps import get_resource_registry

router = APIRouter(prefix="/resources", tags=["resources"])

#: 由 API 路由自身消费、不当作资源过滤条件的查询参数
_RESERVED_PARAMS = ("bot_id", "token")


@router.get("")
async def list_resource_kinds(registry=Depends(get_resource_registry)):
    return JSONResponse(content={"ok": True, "kinds": registry.kinds()})


@router.get("/{kind}")
async def list_resource_options(kind: str, request: Request, registry=Depends(get_resource_registry)):
    if not registry.has(kind):
        return JSONResponse(
            status_code=404,
            content={"ok": False, "kind": kind, "message": f"未注册的资源种类: {kind}"},
        )

    params = dict(request.query_params)
    bot_id = params.pop("bot_id", "") or None
    for key in _RESERVED_PARAMS:
        params.pop(key, None)

    options = await registry.options(kind, bot_id=bot_id, filters=params)
    return JSONResponse(content={"ok": True, "kind": kind, "count": len(options), "options": options})
