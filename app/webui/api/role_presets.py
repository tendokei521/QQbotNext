"""角色预设 API：独立管理可复用的角色系统提示词。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.services.role_preset_service import RolePresetService
from app.webui.api.deps import get_container

router = APIRouter(prefix="/role-presets", tags=["role-presets"])


def _ok(message: str, **extra):
    return JSONResponse(content={"status": "success", "message": message, **extra})


def _err(status: int, message: str):
    return JSONResponse(status_code=status, content={"status": "error", "message": message})


def _service(request: Request) -> RolePresetService:
    return get_container(request).get(RolePresetService)


async def _json_body(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


@router.get("")
async def list_presets(request: Request):
    return JSONResponse(content={"ok": True, "presets": _service(request).list_presets()})


@router.post("")
async def create_preset(request: Request):
    body = await _json_body(request)
    try:
        preset = await _service(request).create_preset(
            body.get("name", ""),
            body.get("description", ""),
            body.get("system_prompt", ""),
        )
    except ValueError as e:
        return _err(400, str(e))
    return _ok("角色预设创建成功", preset=preset)


@router.get("/{role_id}")
async def get_preset(role_id: str, request: Request):
    preset = _service(request).get_preset(role_id)
    if preset is None:
        return _err(404, f"角色预设不存在: {role_id}")
    return JSONResponse(content={"ok": True, "preset": preset})


@router.put("/{role_id}")
async def update_preset(role_id: str, request: Request):
    try:
        preset = await _service(request).update_preset(role_id, await _json_body(request))
    except ValueError as e:
        return _err(400, str(e))
    return _ok("角色预设已更新", preset=preset)


@router.delete("/{role_id}")
async def delete_preset(role_id: str, request: Request):
    try:
        await _service(request).delete_preset(role_id)
    except ValueError as e:
        return _err(400, str(e))
    return _ok("角色预设已删除")