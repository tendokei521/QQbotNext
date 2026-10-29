"""系统提示词方案 API（设置页编辑用）。

- ``GET /api/prompt-presets``：三套方案的默认文本 / 当前覆盖 / 生效文本；
- ``PUT /api/prompt-presets/{key}``：保存覆盖文本（空串 = 恢复默认）；
- ``DELETE /api/prompt-presets/{key}``：恢复默认。

覆盖是**全局**的（对所有账号生效）；账号级 ``behavior_rules_override`` 仍然更优先。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.application import prompt_preset_service
from app.webui.api.deps import get_config_service

router = APIRouter(prefix="/prompt-presets", tags=["prompt-presets"])


def _ok(presets: list[dict], message: str = "", **extra):
    return JSONResponse(content={"ok": True, "message": message, "presets": presets, **extra})


@router.get("")
async def api_prompt_presets(config_service=Depends(get_config_service)):
    await prompt_preset_service.load_overrides(config_service)
    return _ok(prompt_preset_service.describe_presets())


@router.put("/{key}")
async def api_prompt_preset_save(
    key: str,
    request: Request,
    config_service=Depends(get_config_service),
):
    try:
        body = await request.json()
    except Exception as e:  # noqa: BLE001
        return JSONResponse(
            status_code=400,
            content={"ok": False, "message": f"请求体不是合法 JSON: {e}"},
        )
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"ok": False, "message": "请求体必须是对象"})
    try:
        result = await prompt_preset_service.save_override(
            config_service, key, str(body.get("text") or "")
        )
    except prompt_preset_service.PromptPresetError as e:
        return JSONResponse(status_code=400, content={"ok": False, "message": str(e)})
    return _ok(prompt_preset_service.describe_presets(), "已保存", **result)


@router.delete("/{key}")
async def api_prompt_preset_reset(key: str, config_service=Depends(get_config_service)):
    try:
        result = await prompt_preset_service.reset_override(config_service, key)
    except prompt_preset_service.PromptPresetError as e:
        return JSONResponse(status_code=400, content={"ok": False, "message": str(e)})
    return _ok(prompt_preset_service.describe_presets(), "已恢复默认", **result)
