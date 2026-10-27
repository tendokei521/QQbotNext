"""群列表 API：供「权限与回复」页的群开关矩阵使用。

两个端点都按账号维度（``?account_id=``）：

- ``GET /api/groups``：只读视图（缓存优先，离线可用）；
- ``POST /api/groups/refresh``：主动向 OneBot 拉一次，写缓存并返回最新列表。

设计约束（docs/permission-dialogue-policy-design.md §4）：
GET 不产生副作用——群列表只能通过显式 refresh 更新，避免"打开页面就发网络请求"。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.application.group_service import GroupService
from app.webui.api.deps import get_bot_service, get_config_service

router = APIRouter(tags=["groups"])


def _ok(message: str, **extra):
    return JSONResponse(content={"status": "success", "message": message, **extra})


def _err(status: int, message: str):
    return JSONResponse(status_code=status, content={"status": "error", "message": message})


def _service(bot_service, config_service) -> GroupService:
    return GroupService(bot_service, config_service)


@router.get("/groups")
async def api_groups(
    account_id: str | None = None,
    bot_service=Depends(get_bot_service),
    config_service=Depends(get_config_service),
):
    """读取某账号的群列表（缓存优先）。"""
    if not account_id:
        return _err(400, "缺少 account_id")
    view = _service(bot_service, config_service).list_groups(account_id)
    return JSONResponse(content={"ok": True, **view})


@router.post("/groups/refresh")
async def api_groups_refresh(
    account_id: str | None = None,
    bot_service=Depends(get_bot_service),
    config_service=Depends(get_config_service),
):
    """向 OneBot 拉取一次群列表并写缓存。"""
    if not account_id:
        return _err(400, "缺少 account_id")
    try:
        result = await _service(bot_service, config_service).refresh(account_id)
    except ValueError as e:
        # 未连接/对端不支持/超时都属于"用户可理解、可重试"的失败，不该是 500
        return _err(409, str(e))
    return _ok(f"已拉取 {result['count']} 个群", **result)
