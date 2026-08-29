"""框架级 Agent 管理 API：配置 / 启停 / 权限 / 定时任务 / 主动消息。

全部读框架 AgentManager 运行时（与 llm_chat_v2 模块解耦）。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.llm.config import LEGACY_LLM_CONNECTION_KEYS
from app.llm.config_schema import STREAM_PRESETS
from app.services.bot_service import PASSWORD_MASK as _PASSWORD_MASK
from app.services.provider_model_service import ProviderModelService
from app.services.provider_preset_service import ProviderPresetService
from app.webui.api.deps import get_container, parse_bot_id

router = APIRouter(prefix="/agent", tags=["agent"])


def _ok(message: str, **extra):
    return JSONResponse(content={"status": "success", "message": message, **extra})


def _err(status: int, message: str):
    return JSONResponse(status_code=status, content={"status": "error", "message": message})


def _runtime(container, bot_id):
    """取指定 bot 的 Agent 运行时；无则返回 (None, None)。"""
    if bot_id is None:
        return None, None
    from app.llm.manager import AgentManager

    manager = container.get(AgentManager)
    if manager is None:
        return None, None
    return manager.get_runtime(bot_id), manager


def _split_schema(schema: dict) -> dict:
    groups, items = {}, {}
    for key, value in (schema or {}).items():
        if isinstance(value, dict) and value.get("type") == "group":
            groups[key] = value
        else:
            items[key] = value
    return {"groups": groups, "items": items}


def _mask_password_config(config: dict, schema: dict) -> dict:
    result = dict(config)
    for key, field in (schema or {}).items():
        if isinstance(field, dict) and field.get("type") == "password":
            if result.get(key):
                result[key] = _PASSWORD_MASK
    return result


# ==================== 配置 / 权限 ====================

@router.get("/config")
async def agent_config(request: Request, bot_id: int | None = Depends(parse_bot_id)):
    from app.llm.config_schema import SCHEMA

    container = get_container(request)
    runtime, _ = _runtime(container, bot_id)
    if runtime is None:
        return _err(404, f"Bot {bot_id} 无 Agent 运行时")
    perm = runtime.config.permission
    raw_config = dict(runtime.config.raw_config)
    for key in LEGACY_LLM_CONNECTION_KEYS:
        raw_config.pop(key, None)
    schema = _split_schema(SCHEMA)
    presets = container.get(ProviderPresetService).list_presets()
    preset_name_map = {p["id"]: p["name"] for p in presets}
    provider_models = container.get(ProviderModelService).list_models()
    for m in provider_models:
        m["preset_name"] = preset_name_map.get(m.get("preset_id", ""), m.get("preset_id", ""))
    return JSONResponse(content={
        "ok": True,
        "bot_id": bot_id,
        "enabled": runtime.config.enabled,
        "permission": {
            "group_mode": perm.group_mode,
            "group_list": perm.group_list,
            "user_mode": perm.user_mode,
            "user_list": perm.user_list,
        },
        "config": _mask_password_config(raw_config, SCHEMA),
        "schema": schema,
        "provider_presets": presets,
        "provider_models": provider_models,
        "stream_presets": STREAM_PRESETS,
    })


@router.post("/config")
async def agent_config_update(request: Request, bot_id: int | None = Depends(parse_bot_id)):
    from app.llm.config_schema import SCHEMA

    container = get_container(request)
    runtime, _ = _runtime(container, bot_id)
    if runtime is None:
        return _err(404, f"Bot {bot_id} 无 Agent 运行时")
    try:
        data = await request.json()
    except Exception:
        data = {}
    data = data or {}
    # 配置字段（password 为脱敏哨兵时保留旧值；legacy 连接字段不允许再写入）
    incoming_config = data.get("config", {}) or {}
    for key in LEGACY_LLM_CONNECTION_KEYS:
        incoming_config.pop(key, None)
    for key, value in incoming_config.items():
        field = SCHEMA.get(key)
        if isinstance(field, dict) and field.get("type") == "password" and value == _PASSWORD_MASK:
            continue
        runtime.config.set(key, value, auto_save=False)
    runtime.config.save()
    # 启停 / 权限
    perm = data.get("permission")
    if isinstance(perm, dict):
        runtime.config.update_permission(
            perm.get("group_mode", "blacklist"),
            perm.get("group_list", []),
            perm.get("user_mode", "blacklist"),
            perm.get("user_list", []),
        )
    if data.get("enabled") is not None:
        runtime.config.set_enabled(bool(data["enabled"]))
    return _ok(f"Bot {bot_id} Agent 配置已更新")


# ==================== LLM 遥测 ====================

@router.get("/telemetry")
async def agent_telemetry(
    request: Request,
    bot_id: int | None = Depends(parse_bot_id),
    limit: int = 30,
):
    container = get_container(request)
    runtime, _ = _runtime(container, bot_id)
    if runtime is None:
        return _err(404, f"Bot {bot_id} 无 Agent 运行时")
    telemetry = getattr(runtime, "telemetry", None)
    if telemetry is None:
        return JSONResponse(content={"ok": True, "stats": {}, "recent": [], "recent_tools": []})
    return JSONResponse(content={
        "ok": True,
        "stats": telemetry.stats(),
        "recent": telemetry.recent(limit=max(1, min(limit, 200))),
        "recent_tools": telemetry.recent_tools(max(1, min(limit, 200))),
    })


@router.post("/telemetry/reset")
async def agent_telemetry_reset(request: Request, bot_id: int | None = Depends(parse_bot_id)):
    container = get_container(request)
    runtime, _ = _runtime(container, bot_id)
    if runtime is None:
        return _err(404, f"Bot {bot_id} 无 Agent 运行时")
    telemetry = getattr(runtime, "telemetry", None)
    if telemetry is None:
        return _ok("该 Bot 无遥测数据")
    result = telemetry.reset()
    return _ok(f"Bot {bot_id} LLM 遥测已重置", **result)


# ==================== 知识库管理 ====================

@router.get("/knowledge/items")
async def agent_knowledge_items(request: Request, bot_id: int | None = Depends(parse_bot_id), limit: int = 100):
    container = get_container(request)
    runtime, _ = _runtime(container, bot_id)
    if runtime is None:
        return _err(404, f"Bot {bot_id} 无 Agent 运行时")
    try:
        items = runtime.knowledge.list(limit=max(1, min(limit, 500)))
    except Exception as e:
        return _err(500, f"读取知识库失败: {e}")
    return JSONResponse(content={"ok": True, "items": items})


@router.post("/knowledge/items")
async def agent_knowledge_add(request: Request, bot_id: int | None = Depends(parse_bot_id)):
    container = get_container(request)
    runtime, _ = _runtime(container, bot_id)
    if runtime is None:
        return _err(404, f"Bot {bot_id} 无 Agent 运行时")
    try:
        body = await request.json()
    except Exception:
        body = {}
    title = str((body or {}).get("title", "") or "").strip()
    content = str((body or {}).get("content", "") or "").strip()
    source = str((body or {}).get("source", "manual") or "manual").strip()
    if not content:
        return _err(400, "内容不能为空")
    cid, message = await runtime.knowledge.add_text(content, title=title, source=source)
    if cid is None:
        return _err(400, message)
    return _ok("知识库条目已添加", cid=cid)


@router.delete("/knowledge/items/{cid}")
async def agent_knowledge_delete(cid: str, request: Request, bot_id: int | None = Depends(parse_bot_id)):
    container = get_container(request)
    runtime, _ = _runtime(container, bot_id)
    if runtime is None:
        return _err(404, f"Bot {bot_id} 无 Agent 运行时")
    try:
        ok = runtime.knowledge.delete(cid)
    except Exception as e:
        return _err(500, f"删除知识库失败: {e}")
    if not ok:
        return _err(404, "知识库条目不存在")
    return _ok("知识库条目已删除")


# ==================== 定时任务 ====================

@router.get("/tasks")
async def agent_tasks(request: Request, bot_id: int | None = Depends(parse_bot_id)):
    container = get_container(request)
    runtime, _ = _runtime(container, bot_id)
    if runtime is None:
        return _err(404, f"Bot {bot_id} 无 Agent 运行时")
    return JSONResponse(content={"ok": True, "tasks": runtime.scheduler.status()})


@router.post("/tasks")
async def agent_task_add(request: Request, bot_id: int | None = Depends(parse_bot_id)):
    container = get_container(request)
    runtime, _ = _runtime(container, bot_id)
    if runtime is None:
        return _err(404, f"Bot {bot_id} 无 Agent 运行时")
    if not runtime.config.get("schedule_enable", True):
        return _err(400, "定时任务未启用，请在配置中开启")
    try:
        data = await request.json()
    except Exception:
        data = {}
    trigger = (data or {}).get("trigger", "").strip()
    content = (data or {}).get("content", "").strip()
    if not trigger or not content:
        return _err(400, "缺少 trigger 或 content")
    is_group = bool((data or {}).get("is_group", False))
    target = str((data or {}).get("target", "")).strip()
    if not target:
        return _err(400, "缺少 target（群号或QQ号）")
    session_id = f"group_{target}" if is_group else f"private_{target}"
    entry = await runtime.scheduler.schedule(session_id, {
        "trigger": trigger,
        "content": content,
        "repeat": (data or {}).get("repeat", "") or "",
    })
    if entry is None:
        return _err(400, f"时间表达式无法解析: {trigger}")
    return JSONResponse(content={
        "ok": True,
        "task_id": entry.id,
        "next_trigger_time": int(entry.next_at.timestamp()),
        "repeat": entry.repeat,
    })


@router.post("/tasks/{task_id}/trigger")
async def agent_task_trigger(task_id: str, request: Request, bot_id: int | None = Depends(parse_bot_id)):
    container = get_container(request)
    runtime, _ = _runtime(container, bot_id)
    if runtime is None:
        return _err(404, f"Bot {bot_id} 无 Agent 运行时")
    ok = await runtime.scheduler.trigger_now(task_id)
    if not ok:
        return _err(400, f"任务 {task_id} 不存在或已结束")
    return _ok(f"已立即触发任务 {task_id}")


@router.post("/tasks/{task_id}/cancel")
async def agent_task_cancel(task_id: str, request: Request, bot_id: int | None = Depends(parse_bot_id)):
    container = get_container(request)
    runtime, _ = _runtime(container, bot_id)
    if runtime is None:
        return _err(404, f"Bot {bot_id} 无 Agent 运行时")
    ok = runtime.scheduler.cancel(task_id)
    if not ok:
        return _err(400, f"任务 {task_id} 不存在")
    return _ok(f"已取消任务 {task_id}")


# ==================== 主动消息 ====================

@router.get("/proactive/status")
async def agent_proactive_status(request: Request, bot_id: int | None = Depends(parse_bot_id)):
    container = get_container(request)
    runtime, _ = _runtime(container, bot_id)
    if runtime is None:
        return _err(404, f"Bot {bot_id} 无 Agent 运行时")
    return JSONResponse(content={"ok": True, "sessions": runtime.proactive.status()})


@router.post("/proactive/trigger")
async def agent_proactive_trigger(request: Request, bot_id: int | None = Depends(parse_bot_id)):
    container = get_container(request)
    runtime, _ = _runtime(container, bot_id)
    if runtime is None:
        return _err(404, f"Bot {bot_id} 无 Agent 运行时")
    try:
        data = await request.json()
    except Exception:
        data = {}
    session_id = (data or {}).get("session_id", "")
    if not session_id:
        return _err(400, "缺少 session_id")
    ok = await runtime.proactive.manual_trigger(session_id)
    if not ok:
        return _err(400, f"会话 {session_id} 未启用或不在主动列表")
    return _ok(f"已触发 {session_id} 主动发言")
