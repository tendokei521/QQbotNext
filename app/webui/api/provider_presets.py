"""Provider 预设 / 模型 / 全局设置管理 API。

对齐 AstrBot 三层结构：
- provider-presets = 连接来源（source）
- provider-models = 来源下的模型实例（model/provider）
- provider-settings = 全局默认设置
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from app.services.provider_model_service import ProviderModelService
from app.services.provider_preset_service import ProviderPresetService
from app.webui.api.deps import get_container

router = APIRouter(prefix="/provider-presets", tags=["provider-presets"])
models_router = APIRouter(prefix="/provider-models", tags=["provider-models"])
settings_router = APIRouter(prefix="/provider-settings", tags=["provider-settings"])


def _ok(message: str, **extra):
    return JSONResponse(content={"status": "success", "message": message, **extra})


def _err(status: int, message: str):
    return JSONResponse(status_code=status, content={"status": "error", "message": message})


def _service(request: Request) -> ProviderPresetService:
    return get_container(request).get(ProviderPresetService)


def _model_service(request: Request) -> ProviderModelService:
    return get_container(request).get(ProviderModelService)


async def _json_body(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


# ==================== Provider 预设（连接来源） ====================

@router.get("")
async def list_provider_presets(request: Request):
    presets = _service(request).list_presets()
    return JSONResponse(content={"ok": True, "presets": presets})


@router.post("")
async def create_provider_preset(request: Request):
    try:
        preset = await _service(request).create_preset(await _json_body(request))
    except ValueError as e:
        return _err(400, str(e))
    return _ok("Provider 预设创建成功", preset=preset)


@router.get("/{preset_id}")
async def get_provider_preset(preset_id: str, request: Request):
    preset = _service(request).get_preset(preset_id)
    if preset is None:
        return _err(404, f"Provider 预设不存在: {preset_id}")
    return JSONResponse(content={"ok": True, "preset": preset})


@router.put("/{preset_id}")
async def update_provider_preset(preset_id: str, request: Request):
    try:
        preset = await _service(request).update_preset(preset_id, await _json_body(request))
    except ValueError as e:
        return _err(400, str(e))
    return _ok("Provider 预设已更新", preset=preset)


@router.delete("/{preset_id}")
async def delete_provider_preset(preset_id: str, request: Request):
    try:
        await _service(request).delete_preset(preset_id)
    except ValueError as e:
        return _err(400, str(e))
    return _ok("Provider 预设已删除")


@router.post("/{preset_id}/test")
async def test_provider_preset(preset_id: str, request: Request):
    body = await _json_body(request)
    try:
        result = await _service(request).test_preset(preset_id, model=body.get("model"))
    except ValueError as e:
        return _err(404, str(e))
    if not result.get("ok"):
        return _err(502, result.get("message", "连接失败"))
    return JSONResponse(content={"ok": True, **result})


@router.post("/{preset_id}/models/fetch")
async def fetch_provider_models(preset_id: str, request: Request):
    try:
        models = await _model_service(request).fetch_models(preset_id)
    except ValueError as e:
        return _err(400, str(e))
    return JSONResponse(content={"ok": True, "models": models})


# ==================== Provider 模型实例 ====================

@models_router.get("")
async def list_provider_models(request: Request, preset_id: str | None = Query(default=None)):
    models = _model_service(request).list_models(preset_id)
    return JSONResponse(content={"ok": True, "models": models})


@models_router.post("")
async def create_provider_model(request: Request):
    body = await _json_body(request)
    preset_id = str(body.get("preset_id", "")).strip()
    if not preset_id:
        return _err(400, "缺少 preset_id")
    try:
        model = await _model_service(request).create_model(preset_id, body)
    except ValueError as e:
        return _err(400, str(e))
    return _ok("Provider 模型创建成功", model=model)


@models_router.get("/{model_id}")
async def get_provider_model(model_id: str, request: Request):
    model = _model_service(request).get_model(model_id)
    if model is None:
        return _err(404, f"Provider 模型不存在: {model_id}")
    return JSONResponse(content={"ok": True, "model": model})


@models_router.put("/{model_id}")
async def update_provider_model(model_id: str, request: Request):
    try:
        model = await _model_service(request).update_model(model_id, await _json_body(request))
    except ValueError as e:
        return _err(400, str(e))
    return _ok("Provider 模型已更新", model=model)


@models_router.delete("/{model_id}")
async def delete_provider_model(model_id: str, request: Request):
    try:
        await _model_service(request).delete_model(model_id)
    except ValueError as e:
        return _err(400, str(e))
    return _ok("Provider 模型已删除")


@models_router.post("/{model_id}/test")
async def test_provider_model(model_id: str, request: Request):
    try:
        result = await _model_service(request).test_model(model_id)
    except ValueError as e:
        return _err(404, str(e))
    if not result.get("ok"):
        return _err(502, result.get("message", "连接失败"))
    return JSONResponse(content={"ok": True, **result})


# ==================== Provider 全局设置 ====================

@settings_router.get("")
async def get_provider_settings(request: Request):
    return JSONResponse(content={"ok": True, "settings": _model_service(request).get_settings()})


@settings_router.put("")
async def save_provider_settings(request: Request):
    settings = await _model_service(request).save_settings(await _json_body(request))
    return _ok("Provider 全局设置已保存", settings=settings)