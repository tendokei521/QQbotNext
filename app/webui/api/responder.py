"""响应权策略 API：按「模块 × 群」配置谁响应（取代旧「单一服务」全局开关）。

设计见 ``docs/multi-account-design.md`` §6/§8：
- GET  ``/api/responder/overview``：声明模块总览（独立面板用，全局、不含账号参数）；
- GET  ``/api/modules/{module}/responder``：声明状态 + 每个群的策略 + 当前响应者预览；
- POST 同上：整值替换该模块的群策略（只接受声明支持的能力）。

策略语义（all / dedupe / delegate / leader + fallback）由 ``app.plugin.responder`` 定义，
本文件只做校验/序列化，不重新实现仲裁。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.core.logger import logger
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

#: 框架级虚拟模块（无真实实例，恒声明支持响应权策略；**不归插件面板管**）
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


def _loaded_by_name(registry) -> dict[str, object]:
    """每个能力取一个已加载实例。

    ``supports_single_service`` 是**类属性**，任一账号实例上的值都等价；
    不能用 ``/api/modules`` 的编目条目判断——未加载的条目该字段恒为 False，
    会把"支持但当前没装配"的插件误报成"未声明"。
    """
    try:
        modules = list(registry.loaded() or [])  # type: ignore[attr-defined]
    except Exception as e:  # noqa: BLE001 —— 总览读不到就空列表，不影响单模块接口
        logger.warning(f"[Responder] 读取已加载插件失败（总览按空处理）: {e}")
        modules = []
    loaded: dict[str, object] = {}
    for module in modules:
        name = str(getattr(module, "module_name", "") or "")
        if name and name not in loaded:
            loaded[name] = module
    return loaded


def _display(capability_id: str, module: object) -> dict:
    """能力的展示信息（卡片标题 / 独立配置页头部用）。"""
    return {
        "capability_id": capability_id,
        "name": str(getattr(module, "name", "") or capability_id),
        "sign": str(getattr(module, "sign", "") or ""),
        "description": str(getattr(module, "description", "") or ""),
    }


def _account_ids(config_service) -> list[str]:
    try:
        return [str(bot.get("id")) for bot in config_service.get_bots() if bot.get("id")]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[Responder] 读取账号清单失败（按无账号处理）: {e}")
        return []


def _group_catalog(config_service, gateway) -> list[dict]:
    """「哪些群、群里有哪些账号」——**缓存优先**，在线连接只用来刷新名称。

    群策略是全局的（对所有账号生效），所以不能用"当前账号在线连接"当唯一来源：
    账号离线时旧实现会给出空列表，页面就变成"暂无可配置的群"。
    缓存来自登录时拉取并落库的 ``known_groups``（docs/multi-account-design.md §4.1）。
    """
    accounts = _account_ids(config_service)
    known = set(accounts)
    catalog: dict[str, dict] = {}

    def _merge(account_id: str, group_id: str, name: str = "") -> None:
        if not group_id or (account_id and account_id not in known):
            return
        entry = catalog.setdefault(
            group_id,
            {"group_id": group_id, "name": name or group_id, "accounts": []},
        )
        if name:
            entry["name"] = name
        if account_id and account_id not in entry["accounts"]:
            entry["accounts"].append(account_id)

    # 1) 缓存：离线也能配（已退群的行不参与，避免给"已经不在的群"配策略）
    try:
        for row in config_service.list_known_groups():
            if not bool(row.get("in_group", True)):
                continue
            _merge(str(row.get("account_id") or ""), str(row.get("group_id") or ""), str(row.get("name") or ""))
    except Exception as e:  # noqa: BLE001 —— 缓存读失败只影响离线可用性
        logger.warning(f"[Responder] 读取群缓存失败（继续用在线连接）: {e}")

    # 2) 在线连接：覆盖最新群名，并补上缓存里还没有的群
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
            _merge(account_id, group_id, names.get(group_id) or "")

    return [catalog[group_id] for group_id in sorted(catalog)]


def _responder_payload(decision: ResponderDecision) -> dict:
    return decision_as_dict(decision)


def _groups_for(capability_id: str, config_service, gateway, responder) -> list[dict]:
    """某能力的「群 × 策略 × 当前响应者」列表（单模块接口与总览共用）。"""
    policies = {
        str(row["channel_id"]): row
        for row in config_service.list_channel_policies(capability_id)
    }
    groups = []
    for entry in _group_catalog(config_service, gateway):
        channel_id = entry["group_id"]
        row = policies.get(channel_id) or {}
        decision = responder.preview_capability(capability_id, channel_id)
        groups.append({
            **entry,
            "policy": str(row.get("policy") or "all"),
            "primary_account_id": row.get("primary_account_id"),
            "fallback": str(row.get("fallback") or "open"),
            "preview": _responder_payload(decision),
        })
    return groups


@router.get("/responder/overview")
async def responder_overview(
    config_service=Depends(get_config_service),
    gateway=Depends(get_gateway),
    registry=Depends(get_module_registry),
    responder=Depends(get_responder_service),
):
    """插件响应策略总览（独立面板用）：已声明 / 未声明模块 + 每模块摘要。

    全局接口（不接收 ``account_id``）：``channel_policies`` 本身就是
    ``(能力, 群)`` 维度、对所有账号生效，面板不该跟着"当前账号"变化。
    框架级 Agent（``agent``）不归这个面板管，直接排除。
    """
    declared: list[dict] = []
    undeclared: list[dict] = []
    for name, module in sorted(_loaded_by_name(registry).items()):
        if name == VIRTUAL_AGENT_MODULE:
            continue
        meta = _display(name, module)
        if not declares_single_service(module):
            undeclared.append(meta)
            continue
        groups = _groups_for(name, config_service, gateway, responder)
        # 只有"同群 ≥2 个账号"的群才需要响应权策略，摘要也只统计这些
        multi = [group for group in groups if len(group["accounts"]) >= 2]
        configured = [
            group for group in multi
            if group["policy"] != "all" or group["primary_account_id"] not in (None, "")
        ]
        distribution: dict[str, int] = {}
        for group in multi:
            distribution[group["policy"]] = distribution.get(group["policy"], 0) + 1
        declared.append({
            **meta,
            "groups_total": len(multi),
            "configured": len(configured),
            "degraded": sum(1 for group in configured if group["preview"].get("degraded")),
            "policies": distribution,
        })
    return _ok("", declared=declared, undeclared=undeclared)


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

    meta = _display(module_name, registry.get(module_name))
    groups = _groups_for(module_name, config_service, gateway, responder)
    return JSONResponse(content={"module": module_name, "declared": True, **meta, "groups": groups})



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
