"""框架级 Agent 管理 API：配置 / 启停 / 权限 / 定时任务 / 主动消息。

全部读框架 AgentManager 运行时（与 llm_chat_v2 模块解耦）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.agent.config import LEGACY_LLM_CONNECTION_KEYS
from app.agent.config_schema import STREAM_PRESETS
from app.agent.config import module_owned_keys
from app.plugins.llm_tools_extra.onebot.manifest import ONEBOT_TOOLS
from app.plugins.llm_tools_extra.onebot.security import resolve_tool_policy
from app.agent.tools.system import list_system_tools
from app.application.bot_service import PASSWORD_MASK as _PASSWORD_MASK
from app.core.logger import logger
from app.plugin.schema_utils import webui_schema
from app.webui.api.deps import (
    get_agent_manager,
    get_config_service,
    get_provider_model_service,
    get_provider_preset_service,
    get_resource_registry,
    get_role_preset_service,
    parse_account_id,
)

router = APIRouter(prefix="/agent", tags=["agent"])


def _ok(message: str, **extra):
    return JSONResponse(content={"status": "success", "message": message, **extra})


def _err(status: int, message: str):
    return JSONResponse(status_code=status, content={"status": "error", "message": message})


def _account_exists(config_service, account_id) -> bool:
    try:
        return any(str(b.get("id")) == str(account_id) for b in config_service.get_bots())
    except Exception:  # noqa: BLE001 —— 配置读失败按不存在处理（调用方回 404）
        return False


def _runtime(agent_manager, bot_id):
    """取**或装配**该账号的 Agent 运行时；账号不存在时返回 (None, manager)。

    v4 起账号 id 在「添加账号」时就确定，配置归属不再依赖登录成功，所以这里对"账号存在
    但还没连上"的情况直接 ``ensure_runtime`` 装配一个 ``online=False`` 的运行时——
    配置读写立刻可用，但定时任务/主动消息不会启动（见 `app/agent/runtime.py`）。

    `agent_manager` 由 `Depends(get_agent_manager)` 注入（见 deps.py），
    端点里不再自己从容器按类型捞。
    """
    if bot_id is None or agent_manager is None:
        return None, agent_manager
    config_service = getattr(agent_manager, "config_service", None)
    if config_service is not None and not _account_exists(config_service, bot_id):
        return None, agent_manager
    runtime = agent_manager.get_runtime(bot_id)
    if runtime is None:
        runtime = agent_manager.ensure_runtime(bot_id)
    return runtime, agent_manager


def _mask_password_config(config: dict, schema: dict) -> dict:
    result = dict(config)
    for key, field in (schema or {}).items():
        if isinstance(field, dict) and field.get("type") == "password":
            if result.get(key):
                result[key] = _PASSWORD_MASK
    return result


# ==================== 配置 / 权限 ====================

@router.get("/config")
async def agent_config(
    request: Request,
    bot_id: str | None = Depends(parse_account_id),
    agent_manager=Depends(get_agent_manager),
    config_service=Depends(get_config_service),
    role_preset_service=Depends(get_role_preset_service),
    resource_registry=Depends(get_resource_registry),
    provider_preset_service=Depends(get_provider_preset_service),
    provider_model_service=Depends(get_provider_model_service),
):
    from app.agent.config_schema import SCHEMA

    runtime, _ = _runtime(agent_manager, bot_id)
    if runtime is None:
        return _err(404, f"账号不存在或未装配运行时: {bot_id}")
    # 兼容旧格式：若 agent 配置里保存了旧 system_prompt，自动转为角色预设
    stored_config = config_service.get_module_config("agent", bot_id) or {}
    await role_preset_service.ensure_legacy_prompt(bot_id, stored_config.get("system_prompt"))
    perm = runtime.config.permission
    raw_config = dict(runtime.config.raw_config)
    for key in LEGACY_LLM_CONNECTION_KEYS:
        raw_config.pop(key, None)
    # 模块拥有的键不出现在 agent 配置视图里：它们由各自模块的配置页管，
    # 回传只会让前端把旧值原样 echo 回来，进而被写入校验误判。
    for key in module_owned_keys():
        raw_config.pop(key, None)
    schema = webui_schema(SCHEMA, registry=resource_registry, source_module="agent")
    presets = provider_preset_service.list_presets()
    preset_name_map = {p["id"]: p["name"] for p in presets}
    provider_models = provider_model_service.list_models()
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
        "role_presets": role_preset_service.list_presets(),
    })


@router.post("/config")
async def agent_config_update(request: Request, bot_id: str | None = Depends(parse_account_id), agent_manager=Depends(get_agent_manager)):
    from app.agent.config_schema import SCHEMA

    runtime, _ = _runtime(agent_manager, bot_id)
    if runtime is None:
        return _err(404, f"账号不存在或未装配运行时: {bot_id}")
    try:
        data = await request.json()
    except Exception:
        data = {}
    data = data or {}
    # 配置字段（password 为脱敏哨兵时保留旧值；legacy 连接字段不允许再写入）
    incoming_config = data.get("config", {}) or {}
    for key in LEGACY_LLM_CONNECTION_KEYS:
        incoming_config.pop(key, None)
    # 模块拥有的键不许**写**进 agent 配置（界面写一处、策略读另一处 → 关了还能用）。
    # 但前端会把整份配置原样提交，里面常带着值没变的旧键——那些是回声，直接丢掉；
    # 只有"值真的变了"才算真的要写模块键，这才是需要拦下来的错误。
    owned = module_owned_keys()
    rejected: list[str] = []
    for key in [k for k in incoming_config if k in owned]:
        incoming = incoming_config.get(key)
        current = runtime.config.get(key)
        if incoming == current:
            incoming_config.pop(key, None)   # 回声，忽略
        else:
            rejected.append(key)
    if rejected:
        return _err(
            400,
            "这些配置已归属模块，请到对应模块的配置页修改：" + "、".join(sorted(rejected)),
        )
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
    bot_id: str | None = Depends(parse_account_id),
    limit: int = 30,
    agent_manager=Depends(get_agent_manager),
):
    runtime, _ = _runtime(agent_manager, bot_id)
    if runtime is None:
        return _err(404, f"账号不存在或未装配运行时: {bot_id}")
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
async def agent_telemetry_reset(request: Request, bot_id: str | None = Depends(parse_account_id), agent_manager=Depends(get_agent_manager)):
    runtime, _ = _runtime(agent_manager, bot_id)
    if runtime is None:
        return _err(404, f"账号不存在或未装配运行时: {bot_id}")
    telemetry = getattr(runtime, "telemetry", None)
    if telemetry is None:
        return _ok("该 Bot 无遥测数据")
    result = telemetry.reset()
    return _ok(f"Bot {bot_id} LLM 遥测已重置", **result)


# ==================== 服务句柄（v3：业务实现归模块，框架只持句柄）====================

def _service(runtime, name: str):
    """读模块挂载的服务句柄；未装模块/未就绪时返回 None（调用方负责降级）。"""
    return getattr(runtime, name, None) if runtime is not None else None


def _missing(service_label: str):
    """统一的服务缺失响应（200 + 空数据，避免前端 500 弹错）。"""
    return JSONResponse(
        content={
            "ok": True,
            "available": False,
            "message": f"{service_label}未启用（请安装并启用对应功能模块）",
            "tools": [],
            "items": [],
            "tasks": [],
            "sessions": [],
        }
    )


# ==================== 知识库管理 ====================

@router.get("/knowledge/items")
async def agent_knowledge_items(request: Request, bot_id: str | None = Depends(parse_account_id), limit: int = 100, agent_manager=Depends(get_agent_manager)):
    runtime, _ = _runtime(agent_manager, bot_id)
    if runtime is None:
        return _err(404, f"账号不存在或未装配运行时: {bot_id}")
    kb = _service(runtime, "knowledge_service")
    if kb is None:
        return _missing("知识库")
    try:
        items = kb.list(limit=max(1, min(limit, 500)))
    except Exception as e:
        return _err(500, f"读取知识库失败: {e}")
    return JSONResponse(content={"ok": True, "items": items})


@router.post("/knowledge/items")
async def agent_knowledge_add(request: Request, bot_id: str | None = Depends(parse_account_id), agent_manager=Depends(get_agent_manager)):
    runtime, _ = _runtime(agent_manager, bot_id)
    if runtime is None:
        return _err(404, f"账号不存在或未装配运行时: {bot_id}")
    try:
        body = await request.json()
    except Exception:
        body = {}
    title = str((body or {}).get("title", "") or "").strip()
    content = str((body or {}).get("content", "") or "").strip()
    source = str((body or {}).get("source", "manual") or "manual").strip()
    if not content:
        return _err(400, "内容不能为空")
    kb = _service(runtime, "knowledge_service")
    if kb is None:
        return _err(400, "知识库未启用（请安装并启用「知识库」模块）")
    cid, message = await kb.add_text(content, title=title, source=source)
    if cid is None:
        return _err(400, message)
    return _ok("知识库条目已添加", cid=cid)


@router.delete("/knowledge/items/{cid}")
async def agent_knowledge_delete(cid: str, request: Request, bot_id: str | None = Depends(parse_account_id), agent_manager=Depends(get_agent_manager)):
    runtime, _ = _runtime(agent_manager, bot_id)
    if runtime is None:
        return _err(404, f"账号不存在或未装配运行时: {bot_id}")
    kb = _service(runtime, "knowledge_service")
    if kb is None:
        return _err(400, "知识库未启用（请安装并启用「知识库」模块）")
    try:
        ok = kb.delete(cid)
    except Exception as e:
        return _err(500, f"删除知识库失败: {e}")
    if not ok:
        return _err(404, "知识库条目不存在")
    return _ok("知识库条目已删除")


# ==================== OneBot 工具清单 ====================

@router.get("/onebot/tools")
async def agent_onebot_tools(request: Request, bot_id: str | None = Depends(parse_account_id), agent_manager=Depends(get_agent_manager)):
    runtime, _ = _runtime(agent_manager, bot_id)
    if runtime is None:
        return _err(404, f"账号不存在或未装配运行时: {bot_id}")
    tools = []
    for tool in ONEBOT_TOOLS:
        policy = resolve_tool_policy(runtime, tool)
        item = dict(tool)
        item.update(policy)
        tools.append(item)
    return JSONResponse(content={
        "ok": True,
        "log_enabled": bool(runtime.config.get("onebot_tools_log_enabled", True)),
        "tools": tools,
    })


# ==================== 统一 Tool 管理：系统 / 模块 / OneBot / MCP 工具清单 ====================

@router.get("/tools")
async def agent_tools_all(
    request: Request,
    bot_id: str | None = Depends(parse_account_id),
    agent_manager=Depends(get_agent_manager),
):
    """**所有来源**的工具及其状态（显示即真相）。

    每项都带 `reason` / `reason_text`，所以页面不必再自己推断"为什么不可用"。
    """
    runtime, _ = _runtime(agent_manager, bot_id)
    if runtime is None:
        return _err(404, f"账号不存在或未装配运行时: {bot_id}")

    from app.agent.tools.state import resolve_all_tool_states

    states = [s.to_dict() for s in resolve_all_tool_states(runtime)]
    summary: dict[str, int] = {}
    for item in states:
        summary[item["source"]] = summary.get(item["source"], 0) + 1
    return JSONResponse(
        content={
            "ok": True,
            "total": len(states),
            "available": sum(1 for s in states if s["available"]),
            "by_source": summary,
            "tools": states,
        }
    )


@router.post("/tools/toggle")
async def agent_tool_toggle(
    request: Request,
    bot_id: str | None = Depends(parse_account_id),
    agent_manager=Depends(get_agent_manager),
):
    """唯一的 per-tool 开关写入口（写进用户总表 `tools_disabled`）。"""
    runtime, _ = _runtime(agent_manager, bot_id)
    if runtime is None:
        return _err(404, f"账号不存在或未装配运行时: {bot_id}")
    try:
        data = await request.json()
    except Exception:
        data = {}
    name = str((data or {}).get("name", "") or "").strip()
    if not name:
        return _err(400, "缺少工具名 name")
    enabled = bool((data or {}).get("enabled", True))

    from app.agent.tools.state import resolve_tool_state, set_tool_disabled

    set_tool_disabled(runtime, name, disabled=not enabled)
    state = resolve_tool_state(runtime, name=name, source=str((data or {}).get("source", "") or ""))
    return JSONResponse(
        content={
            "ok": True,
            "status": "success",
            "message": f"工具 {name} 已{'启用' if enabled else '关闭'}",
            "state": state.to_dict(),
        }
    )




@router.get("/system/tools")
async def agent_system_tools(request: Request, bot_id: str | None = Depends(parse_account_id), agent_manager=Depends(get_agent_manager)):
    runtime, _ = _runtime(agent_manager, bot_id)
    if runtime is None:
        return _err(404, f"账号不存在或未装配运行时: {bot_id}")
    return JSONResponse(content={
        "ok": True,
        "log_enabled": bool(runtime.config.get("system_tools_log_enabled", True)),
        "tools": list_system_tools(runtime),
    })


@router.get("/module/tools")
async def agent_module_tools(request: Request, bot_id: str | None = Depends(parse_account_id), agent_manager=Depends(get_agent_manager)):
    runtime, _ = _runtime(agent_manager, bot_id)
    if runtime is None:
        return _err(404, f"账号不存在或未装配运行时: {bot_id}")
    module_map = runtime.config.get("module_tools_enabled", {}) or {}
    tools = []
    registry = getattr(runtime, "llm_tools", None)
    specs = registry.all_specs() if registry is not None else []
    for spec in specs:
        module = spec.module
        ready = True
        module_name = getattr(module, "module_name", "模块")
        module_label = getattr(module, "name", module_name)
        if module is not None:
            authority = getattr(module, "authority", None)
            if authority is not None and not getattr(authority, "enabled", True):
                ready = False
            try:
                module_config = getattr(module, "config", None)
                module_tools = module_config.get("tools_enabled", {}) if module_config is not None else {}
                if isinstance(module_tools, dict) and spec.name in module_tools and not module_tools.get(spec.name):
                    ready = False
            except Exception as e:
                # 读取单个模块的工具开关失败：按"未就绪"保守处理，并留痕
                logger.debug(f"[AgentAPI] 读取工具开关失败（{spec.name}）: {e}")
        user_enabled = module_map.get(spec.name, True) if isinstance(module_map, dict) else True
        tools.append({
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
            "category": spec.category or "模块工具",
            "source": spec.source,
            "module": module_name,
            "module_label": module_label,
            "enabled": bool(user_enabled),
            "ready": bool(ready),
            "effective": bool(user_enabled and ready),
            "prerequisite": f"模块 {module_label} 未启用" if not ready else "",
        })
    return JSONResponse(content={
        "ok": True,
        "log_enabled": bool(runtime.config.get("module_tools_log_enabled", True)),
        "tools": tools,
    })


@router.get("/mcp/tools")
async def agent_mcp_tools(request: Request, bot_id: str | None = Depends(parse_account_id), agent_manager=Depends(get_agent_manager)):
    runtime, _ = _runtime(agent_manager, bot_id)
    if runtime is None:
        return _err(404, f"账号不存在或未装配运行时: {bot_id}")
    mcp = _service(runtime, "mcp_service")
    if mcp is None:
        return _missing("MCP 工具")
    try:
        await mcp.ensure_ready()
    except Exception as e:  # noqa: BLE001 —— MCP 连不上不该让页面 500
        return JSONResponse(
            content={
                "ok": True,
                "available": False,
                "message": f"MCP 服务不可用: {e}",
                "tools": [],
            }
        )
    mcp_map = runtime.config.get("mcp_tools_enabled", {}) or {}
    tools = []
    for spec in mcp.build_tools():
        user_enabled = mcp_map.get(spec.name, True) if isinstance(mcp_map, dict) else True
        tools.append({
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
            "category": spec.category or "MCP",
            "source": spec.source,
            "enabled": bool(user_enabled),
            "ready": True,
            "effective": bool(user_enabled),
            "prerequisite": "",
        })
    return JSONResponse(content={
        "ok": True,
        "log_enabled": bool(runtime.config.get("mcp_tools_log_enabled", True)),
        "tools": tools,
    })


# ==================== 定时任务 ====================

@router.get("/tasks")
async def agent_tasks(request: Request, bot_id: str | None = Depends(parse_account_id), agent_manager=Depends(get_agent_manager)):
    runtime, _ = _runtime(agent_manager, bot_id)
    if runtime is None:
        return _err(404, f"账号不存在或未装配运行时: {bot_id}")
    scheduler = _service(runtime, "scheduler")
    if scheduler is None:
        return _missing("定时任务")
    return JSONResponse(content={"ok": True, "available": True, "tasks": scheduler.status()})


@router.post("/tasks")
async def agent_task_add(request: Request, bot_id: str | None = Depends(parse_account_id), agent_manager=Depends(get_agent_manager)):
    runtime, _ = _runtime(agent_manager, bot_id)
    if runtime is None:
        return _err(404, f"账号不存在或未装配运行时: {bot_id}")
    scheduler = _service(runtime, "scheduler")
    if scheduler is None:
        return _err(400, "定时任务未启用（请安装并启用「定时任务」模块）")
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
    entry = await scheduler.schedule(session_id, {
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
async def agent_task_trigger(task_id: str, request: Request, bot_id: str | None = Depends(parse_account_id), agent_manager=Depends(get_agent_manager)):
    runtime, _ = _runtime(agent_manager, bot_id)
    if runtime is None:
        return _err(404, f"账号不存在或未装配运行时: {bot_id}")
    scheduler = _service(runtime, "scheduler")
    if scheduler is None:
        return _err(400, "定时任务未启用（请安装并启用「定时任务」模块）")
    ok = await scheduler.trigger_now(task_id)
    if not ok:
        return _err(400, f"任务 {task_id} 不存在或已结束")
    return _ok(f"已立即触发任务 {task_id}")


@router.post("/tasks/{task_id}/cancel")
async def agent_task_cancel(task_id: str, request: Request, bot_id: str | None = Depends(parse_account_id), agent_manager=Depends(get_agent_manager)):
    runtime, _ = _runtime(agent_manager, bot_id)
    if runtime is None:
        return _err(404, f"账号不存在或未装配运行时: {bot_id}")
    scheduler = _service(runtime, "scheduler")
    if scheduler is None:
        return _err(400, "定时任务未启用（请安装并启用「定时任务」模块）")
    ok = scheduler.cancel(task_id)
    if not ok:
        return _err(400, f"任务 {task_id} 不存在")
    return _ok(f"已取消任务 {task_id}")


# ==================== 主动消息 ====================

@router.get("/proactive/status")
async def agent_proactive_status(request: Request, bot_id: str | None = Depends(parse_account_id), agent_manager=Depends(get_agent_manager)):
    runtime, _ = _runtime(agent_manager, bot_id)
    if runtime is None:
        return _err(404, f"账号不存在或未装配运行时: {bot_id}")
    proactive = _service(runtime, "proactive_service")
    if proactive is None:
        return _missing("主动消息")
    return JSONResponse(content={"ok": True, "available": True, "sessions": proactive.status()})


@router.post("/proactive/trigger")
async def agent_proactive_trigger(request: Request, bot_id: str | None = Depends(parse_account_id), agent_manager=Depends(get_agent_manager)):
    runtime, _ = _runtime(agent_manager, bot_id)
    if runtime is None:
        return _err(404, f"账号不存在或未装配运行时: {bot_id}")
    try:
        data = await request.json()
    except Exception:
        data = {}
    session_id = (data or {}).get("session_id", "")
    if not session_id:
        return _err(400, "缺少 session_id")
    proactive = _service(runtime, "proactive_service")
    if proactive is None:
        return _err(400, "主动消息未启用（请安装并启用「主动消息」模块）")
    ok = await proactive.manual_trigger(session_id)
    if not ok:
        return _err(400, f"会话 {session_id} 未启用或不在主动列表")
    return _ok(f"已触发 {session_id} 主动发言")
