"""首启向导 API（P1）。

- ``POST /api/setup/quick-model``：一次建好「连接预设 + 模型 + 入池」；
- ``POST /api/setup/complete`` 在 readiness 路由里（首启状态与就绪检查同源）。

账号连接复用既有 ``/api/bots/config/add`` + ``/api/bots/{id}/connect``，
这里不重复造一套连接入口。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.application.setup_service import SetupError, SetupService
from app.webui.api.deps import (
    get_agent_manager,
    get_config_service,
    get_provider_model_service,
    get_provider_preset_service,
)

router = APIRouter(tags=["setup"])


def _ok(message: str, **extra):
    return JSONResponse(content={"ok": True, "message": message, **extra})


def _err(status: int, message: str, code: str = ""):
    body = {"ok": False, "message": message}
    if code:
        body["code"] = code
    return JSONResponse(status_code=status, content=body)


@router.post("/setup/quick-model")
async def api_setup_quick_model(
    request: Request,
    config_service=Depends(get_config_service),
    agent_manager=Depends(get_agent_manager),
    provider_preset_service=Depends(get_provider_preset_service),
    provider_model_service=Depends(get_provider_model_service),
):
    try:
        body = await request.json()
    except Exception as e:  # noqa: BLE001
        return _err(400, f"请求体不是合法 JSON: {e}", "SETUP_BAD_JSON")
    if not isinstance(body, dict):
        return _err(400, "请求体必须是 JSON 对象", "SETUP_BAD_BODY")
    account_id = str(body.get("account_id") or "").strip()
    if not account_id:
        return _err(400, "缺少 account_id", "SETUP_ACCOUNT_REQUIRED")

    service = SetupService(
        config_service, agent_manager, provider_preset_service, provider_model_service
    )
    try:
        result = await service.quick_model(
            account_id,
            provider=str(body.get("provider") or "openai"),
            api_base=str(body.get("api_base") or ""),
            api_key=str(body.get("api_key") or ""),
            model=str(body.get("model") or ""),
            preset_name=str(body.get("preset_name") or ""),
        )
    except SetupError as e:
        return _err(400, str(e), "SETUP_FAILED")
    return _ok("模型已接入", **result)
