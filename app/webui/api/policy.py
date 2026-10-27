"""对话策略 API：简单视图（群开关矩阵）与高级视图（规则覆盖）共用。

端点一览（docs/permission-dialogue-policy-design.md §4）：

- ``GET  /api/policy``          解析某账号/某群的生效策略 + 覆盖 + 可覆盖键；
- ``POST /api/policy``          写一层覆盖（账号层或群层），支持"恢复继承"；
- ``POST /api/policy/bulk``     批量开关一批群（全部允许/全部关闭）；
- ``POST /api/policy/mute``     临时静音（到点自动恢复）；
- ``POST /api/policy/scene``    写「账号默认」（群聊/私聊场景层，群行"继承"时用它）；
- ``GET  /api/policy/matrix``   群列表 × 生效状态（列表页一次拉全）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.application.dialogue_policy_service import (
    DEFAULT_CAPABILITY,
    DialoguePolicyService,
    PolicyError,
)
from app.domain.dialogue_policy import PERMISSION_KEYS, SCOPE_OVERRIDABLE
from app.webui.api.deps import get_agent_manager, get_config_service

router = APIRouter(tags=["policy"])


def _ok(message: str = "", **extra):
    return JSONResponse(content={"ok": True, "message": message, **extra})


def _err(status: int, message: str, code: str = ""):
    body = {"ok": False, "message": message}
    if code:
        body["code"] = code
    return JSONResponse(status_code=status, content=body)


def _service(config_service, agent_manager) -> DialoguePolicyService:
    return DialoguePolicyService(config_service, agent_manager)


def _resolved_dict(resolved) -> dict:
    return {
        "capability": resolved.capability,
        "account_id": resolved.account_id,
        "channel_id": resolved.channel_id,
        "enabled": resolved.enabled,
        "enabled_source": resolved.enabled_source,
        "config": resolved.config,
        "permission": resolved.permission,
        "sources": dict(resolved.sources),
    }


@router.get("/policy")
async def api_policy_get(
    account_id: str | None = None,
    capability: str = DEFAULT_CAPABILITY,
    channel_id: str | None = None,
    config_service=Depends(get_config_service),
    agent_manager=Depends(get_agent_manager),
):
    if not account_id:
        return _err(400, "缺少 account_id", "POLICY_ACCOUNT_REQUIRED")
    service = _service(config_service, agent_manager)
    resolved = service.resolve(account_id, capability, channel_id)
    return _ok(
        resolved=_resolved_dict(resolved),
        overrides=service.overrides(account_id, capability, channel_id),
        overridable=list(SCOPE_OVERRIDABLE.get(str(capability), ())),
        permission_keys=list(PERMISSION_KEYS),
    )


@router.post("/policy")
async def api_policy_update(
    request: Request,
    config_service=Depends(get_config_service),
    agent_manager=Depends(get_agent_manager),
):
    try:
        body = await request.json()
    except Exception as e:  # noqa: BLE001 —— 坏 JSON 必须拒绝，不能默认成功
        return _err(400, f"请求体不是合法 JSON: {e}", "POLICY_BAD_JSON")
    if not isinstance(body, dict):
        return _err(400, "请求体必须是 JSON 对象", "POLICY_BAD_BODY")
    account_id = str(body.get("account_id") or "").strip()
    if not account_id:
        return _err(400, "缺少 account_id", "POLICY_ACCOUNT_REQUIRED")
    capability = str(body.get("capability") or DEFAULT_CAPABILITY)
    channel_id = body.get("channel_id")
    patch = body.get("patch") if isinstance(body.get("patch"), dict) else {}
    clear = body.get("clear") if isinstance(body.get("clear"), list) else []
    if not patch and not clear:
        return _err(400, "没有要修改的内容", "POLICY_EMPTY_PATCH")

    service = _service(config_service, agent_manager)
    try:
        row = await service.update(
            account_id,
            capability,
            channel_id,
            enabled=patch.get("enabled", "__unset__"),
            config=patch.get("config"),
            permission=patch.get("permission"),
            clear=[str(key) for key in clear],
        )
    except PolicyError as e:
        return _err(400, str(e), e.code)
    resolved = service.resolve(account_id, capability, channel_id)
    return _ok("策略已保存", override=row, resolved=_resolved_dict(resolved))


@router.post("/policy/bulk")
async def api_policy_bulk(
    request: Request,
    config_service=Depends(get_config_service),
    agent_manager=Depends(get_agent_manager),
):
    try:
        body = await request.json()
    except Exception as e:  # noqa: BLE001
        return _err(400, f"请求体不是合法 JSON: {e}", "POLICY_BAD_JSON")
    if not isinstance(body, dict):
        return _err(400, "请求体必须是 JSON 对象", "POLICY_BAD_BODY")
    account_id = str(body.get("account_id") or "").strip()
    channel_ids = body.get("channel_ids")
    if not account_id:
        return _err(400, "缺少 account_id", "POLICY_ACCOUNT_REQUIRED")
    if not isinstance(channel_ids, list):
        return _err(400, "channel_ids 必须是数组", "POLICY_BAD_BODY")
    enabled = body.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        return _err(400, "enabled 只能是 true / false / null（null = 恢复继承）", "POLICY_BAD_BODY")

    changed = await _service(config_service, agent_manager).bulk_set_enabled(
        account_id, str(body.get("capability") or DEFAULT_CAPABILITY), channel_ids, enabled
    )
    return _ok(f"已更新 {changed} 个群", changed=changed)


@router.post("/policy/mute")
async def api_policy_mute(
    request: Request,
    config_service=Depends(get_config_service),
    agent_manager=Depends(get_agent_manager),
):
    try:
        body = await request.json()
    except Exception as e:  # noqa: BLE001
        return _err(400, f"请求体不是合法 JSON: {e}", "POLICY_BAD_JSON")
    if not isinstance(body, dict):
        return _err(400, "请求体必须是 JSON 对象", "POLICY_BAD_BODY")
    account_id = str(body.get("account_id") or "").strip()
    channel_id = str(body.get("channel_id") or "").strip()
    try:
        minutes = int(body.get("minutes") or 0)
    except (TypeError, ValueError):
        return _err(400, "minutes 必须是整数", "POLICY_BAD_BODY")
    service = _service(config_service, agent_manager)
    try:
        row = await service.set_mute(
            account_id, str(body.get("capability") or DEFAULT_CAPABILITY), channel_id, minutes
        )
    except PolicyError as e:
        return _err(400, str(e), e.code)
    return _ok("已静音" if minutes > 0 else "已取消静音", override=row)


@router.post("/policy/scene")
async def api_policy_scene(
    request: Request,
    config_service=Depends(get_config_service),
    agent_manager=Depends(get_agent_manager),
):
    """写「账号默认」（场景层）。

    ``enabled``：``true/false`` = 显式默认；``null`` = 删掉本层覆盖、跟随 Agent 配置。
    """
    try:
        body = await request.json()
    except Exception as e:  # noqa: BLE001
        return _err(400, f"请求体不是合法 JSON: {e}", "POLICY_BAD_JSON")
    if not isinstance(body, dict):
        return _err(400, "请求体必须是 JSON 对象", "POLICY_BAD_BODY")
    account_id = str(body.get("account_id") or "").strip()
    if not account_id:
        return _err(400, "缺少 account_id", "POLICY_ACCOUNT_REQUIRED")
    if "enabled" not in body:
        return _err(400, "缺少 enabled（true / false / null）", "POLICY_BAD_BODY")
    enabled = body.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        return _err(400, "enabled 只能是 true / false / null", "POLICY_BAD_BODY")

    service = _service(config_service, agent_manager)
    try:
        result = await service.set_scene_enabled(
            account_id,
            str(body.get("scene") or "group"),
            enabled,
            str(body.get("capability") or DEFAULT_CAPABILITY),
        )
    except PolicyError as e:
        return _err(400, str(e), e.code)
    return _ok("账号默认已更新", **result)


@router.get("/policy/matrix")
async def api_policy_matrix(
    account_id: str | None = None,
    capability: str = DEFAULT_CAPABILITY,
    config_service=Depends(get_config_service),
    agent_manager=Depends(get_agent_manager),
):
    if not account_id:
        return _err(400, "缺少 account_id", "POLICY_ACCOUNT_REQUIRED")
    data = _service(config_service, agent_manager).matrix(account_id, capability)
    return _ok(
        account_id=account_id,
        capability=capability,
        default=data["default"],
        rows=data["rows"],
        overridable=list(SCOPE_OVERRIDABLE.get(str(capability), ())),
    )
