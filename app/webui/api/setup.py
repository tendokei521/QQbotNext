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
    get_bot_service,
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

@router.get("/setup/default-account")
async def api_setup_default_account(
    request: Request,
    config_service=Depends(get_config_service),
    bot_service=Depends(get_bot_service),
):
    """向导第一步：读当前默认账号（没有则返回 null）+ 是否已连接。"""
    from app.application.setup_account import DEFAULT_LISTEN_ADDRESS, SetupAccountService

    service = request.app.state.container.get(SetupAccountService)
    current = await service.current()
    if current and current.get("ws_url") and not current["connected"]:
        # 已配置且填了地址但还没连上 → 继续后台每 2s 重试（打开页面即可，不用点按钮）
        service.connect_loop.ensure(current["account_id"])
    return _ok("ok", account=current, default_address=DEFAULT_LISTEN_ADDRESS)


@router.post("/setup/default-account")
async def api_setup_default_account_save(
    request: Request,
    config_service=Depends(get_config_service),
    bot_service=Depends(get_bot_service),
):
    """向导第一步：保存**唯一**默认账号（改它，不新增第二个），并启动后台连接重试。"""
    from app.application.setup_account import SetupAccountError, SetupAccountService

    try:
        body = await request.json()
    except Exception as e:  # noqa: BLE001
        return _err(400, f"请求体不是合法 JSON: {e}", "SETUP_BAD_JSON")
    if not isinstance(body, dict):
        return _err(400, "请求体必须是 JSON 对象", "SETUP_BAD_BODY")

    service = request.app.state.container.get(SetupAccountService)
    try:
        result = await service.upsert(
            account_id=str(body.get("id") or ""),
            name=str(body.get("name") or ""),
            ws_url=str(body.get("ws_url") or ""),
            access_token=str(body.get("access_token") or ""),
            conn_mode=str(body.get("conn_mode") or "reverse"),
        )
    except SetupAccountError as e:
        return _err(400, str(e), "SETUP_ACCOUNT_FAILED")
    return _ok("已保存", **result)
