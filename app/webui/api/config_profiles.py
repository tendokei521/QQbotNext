"""配置档案与路由 API（对齐 AstrBot abconf + routing）。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.services.config_profile_service import ConfigProfileService
from app.webui.api.deps import get_container

router = APIRouter(prefix="/config-profiles", tags=["config-profiles"])


def _ok(message: str, **extra):
    return JSONResponse(content={"status": "success", "message": message, **extra})


def _err(status: int, message: str):
    return JSONResponse(status_code=status, content={"status": "error", "message": message})


def _service(request: Request) -> ConfigProfileService:
    return get_container(request).get(ConfigProfileService)


async def _json_body(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


@router.get("")
async def list_profiles(request: Request):
    return JSONResponse(content={"ok": True, "profiles": _service(request).list_profiles()})


@router.post("")
async def create_profile(request: Request):
    body = await _json_body(request)
    try:
        profile = await _service(request).create_profile(body.get("name"), body.get("config"))
    except ValueError as e:
        return _err(400, str(e))
    return _ok("配置档案创建成功", profile=profile)


@router.get("/{profile_id}")
async def get_profile(profile_id: str, request: Request):
    profile = _service(request).get_profile(profile_id)
    if profile is None:
        return _err(404, f"配置档案不存在: {profile_id}")
    return JSONResponse(content={"ok": True, "profile": profile})


@router.put("/{profile_id}")
async def update_profile(profile_id: str, request: Request):
    try:
        profile = await _service(request).update_profile(profile_id, await _json_body(request))
    except ValueError as e:
        return _err(400, str(e))
    return _ok("配置档案已更新", profile=profile)


@router.delete("/{profile_id}")
async def delete_profile(profile_id: str, request: Request):
    try:
        await _service(request).delete_profile(profile_id)
    except ValueError as e:
        return _err(400, str(e))
    return _ok("配置档案已删除")


# ==================== 路由 ====================

@router.get("/routes/all")
async def list_routes(request: Request):
    return JSONResponse(content={"ok": True, "routes": _service(request).list_routes()})


@router.put("/routes")
async def upsert_route(request: Request):
    body = await _json_body(request)
    umo = str(body.get("umo", "")).strip()
    profile_id = str(body.get("profile_id", "")).strip()
    if not umo or not profile_id:
        return _err(400, "缺少 umo 或 profile_id")
    try:
        await _service(request).set_route(umo, profile_id)
    except ValueError as e:
        return _err(400, str(e))
    return _ok("路由已设置")


@router.delete("/routes/{umo}")
async def delete_route(umo: str, request: Request):
    await _service(request).delete_route(umo)
    return _ok("路由已删除")