"""响应权策略 API：按「模块 × 群」配置谁响应（取代旧「单一服务」全局开关）。

设计见 ``docs/multi-account-design.md`` §6/§8：
- GET  ``/api/modules/{module}/responder``：声明状态 + 每个群的策略 + 当前响应者预览；
- POST 同上：整值替换该模块的群策略（只接受声明支持的能力）。

策略语义（all / dedupe / delegate / leader + fallback）由 ``app.plugin.responder`` 定义，
本文件只做校验/序列化，不重新实现仲裁。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.plugin.authority import declares_single_service
from app.plugin.responder import (
    POLICY_DELEGATE,
    POLICY_LEADER,
    ResponderDecision,
    decision_as_dict,
    normalize_fallback,
    normalize_policy,
)
from app.webui.api.deps import (
    get_config_service,
    get_gateway,
    get_module_registry,
    get_responder_service,
)
from app.webui.ws import manager

router = APIRouter(tags=["responder"])

#: 框架级虚拟模块（无真实实例，恒声明支持响应权策略）
VIRTUAL_AGENT_MODULE = "agent"


def _ok(message: str, **extra):
    return JSONResponse(content={"status": "success", "message": message, **extra})


def _err(status: int, message: str):
    return JSONResponse(status_code=status, content={"status": "error", "message": message})


def _module_exists(registry, module_name: str) -> bool:
    if module_name == VIRTUAL_AGENT_MODULE:
        return True
    try:
        return registry.get(module_name) is not None
    except Exception:  # noqa: BLE001 —— 注册表异常按"不存在"处理，接口返回 404
        return False


def _declared(registry, module_name: str) -> bool:
    if module_name == VIRTUAL_AGENT_MODULE:
        return True
    try:
        module = registry.get(module_name)
    except Exception:  # noqa: BLE001
        return False
    return bool(module is not None and declares_single_service(module))


def _group_catalog(config_service, gateway) -> list[dict]:
    """按账号配置顺序汇总「哪些群、群里有哪些账号」（群名取登录时快照）。"""
    catalog: dict[str, dict] = {}
    try:
        accounts = [str(bot.get("id")) for bot in config_service.get_bots() if bot.get("id")]
    except Exception:  # noqa: BLE001
        accounts = []
    for account_id in accounts:
        conn = getattr(gateway, "connections", {}).get(account_id)
        if conn is None:
            continue
        infos = list(getattr(conn, "all_group_list_info", None) or [])
        names = {
            str(info.get("group_id")): str(info.get("group_name") or "")
            for info in infos
            if info.get("group_id")
        }
        group_ids = [str(info.get("group_id")) for info in infos if info.get("group_id")]
        if not group_ids:
            group_ids = [str(gid) for gid in (getattr(conn, "all_group_list", None) or [])]
        for group_id in group_ids:
            row = catalog.setdefault(
                group_id,
                {"group_id": group_id, "name": names.get(group_id) or group_id, "accounts": []},
            )
            if names.get(group_id):
                row["name"] = names[group_id]
            if account_id not in row["accounts"]:
                row["accounts"].append(account_id)
    return [catalog[group_id] for group_id in sorted(catalog)]


def _responder_payload(decision: ResponderDecision) -> dict:
    return decision_as_dict(decision)


@router.get("/modules/{module_name}/responder")
async def get_responder(
    module_name: str,
    request: Request,
    config_service=Depends(get_config_service),
    gateway=Depends(get_gateway),
    registry=Depends(get_module_registry),
    responder=Depends(get_responder_service),
):
    if not _module_exists(registry, module_name):
        return _err(404, f"模块 {module_name} 不存在")
    declared = _declared(registry, module_name)
    if not declared:
        return JSONResponse(content={"module": module_name, "declared": False, "groups": []})

    policies = {
        str(row["channel_id"]): row
        for row in config_service.list_channel_policies(module_name)
    }
    groups = []
    for entry in _group_catalog(config_service, gateway):
        channel_id = entry["group_id"]
        row = policies.get(channel_id) or {}
        decision = responder.preview_capability(module_name, channel_id)
        groups.append({
            **entry,
            "policy": str(row.get("policy") or "all"),
            "primary_account_id": row.get("primary_account_id"),
            "fallback": str(row.get("fallback") or "open"),
            "preview": _responder_payload(decision),
        })
    return JSONResponse(content={"module": module_name, "declared": True, "groups": groups})


@router.post("/modules/{module_name}/responder")
async def save_responder(
    module_name: str,
    request: Request,
    config_service=Depends(get_config_service),
    registry=Depends(get_module_registry),
):
    if not _module_exists(registry, module_name):
        return _err(404, f"模块 {module_name} 不存在")
    if not _declared(registry, module_name):
        return _err(403, f"模块 {module_name} 未声明支持响应权策略")

    try:
        data = await request.json()
    except Exception:  # noqa: BLE001 —— 非法 JSON 按空处理，前端会收到校验结果
        data = {}
    incoming = (data or {}).get("policies") or []

    try:
        known_accounts = {str(bot.get("id")) for bot in config_service.get_bots() if bot.get("id")}
    except Exception:  # noqa: BLE001
        known_accounts = set()

    accepted: list[dict] = []
    warnings: list[str] = []
    for item in incoming:
        if not isinstance(item, dict):
            continue
        group_id = str(item.get("group_id") or item.get("channel_id") or "").strip()
        if not group_id:
            continue
        policy = normalize_policy(item.get("policy"))
        fallback = normalize_fallback(item.get("fallback"))
        primary = item.get("primary_account_id")
        primary = str(primary) if primary not in (None, "") else None
        if policy in (POLICY_DELEGATE, POLICY_LEADER) and primary is None:
            warnings.append(f"群 {group_id} 选择 {policy} 但未指定主账号，将按 fallback 处理")
        if primary is not None and primary not in known_accounts:
            warnings.append(f"群 {group_id} 指定的账号 {primary} 不存在，已忽略")
            primary = None
        accepted.append({
            "group_id": group_id,
            "policy": policy,
            "primary_account_id": primary,
            "fallback": fallback,
        })

    saved = await config_service.save_channel_policies(module_name, accepted)
    await manager.broadcast(json.dumps({
        "type": "responder_updated",
        "module": module_name,
        "policies": saved,
    }, ensure_ascii=False))
    return _ok("响应权策略已保存", policies=saved, warnings=warnings)
