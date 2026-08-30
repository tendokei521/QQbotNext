"""模块管理 API：启停 / 权限 / 配置 / 重载。"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from app.core.logger import logger
from app.llm.config import LEGACY_LLM_CONNECTION_KEYS
from app.services.bot_service import PASSWORD_MASK as _PASSWORD_MASK
from app.services.bot_service import _mask_password_config
from app.services.module_install_service import ModuleInstallService
from app.webui.api.deps import get_container, parse_bot_id
from app.webui.ws import manager

router = APIRouter(tags=["modules"])


def _ok(message: str, **extra):
    return JSONResponse(content={"status": "success", "message": message, **extra})


def _err(status: int, message: str):
    return JSONResponse(status_code=status, content={"status": "error", "message": message})


def _authority_payload(module, bot_id, enabled):
    perm = module.authority.permission
    return {
        "type": "module_authority_updated",
        "module": module.module_name,
        "bot_id": bot_id,
        "enabled": enabled,
        "permission": {
            "group_mode": perm.group_mode,
            "group_list": perm.group_list,
            "user_mode": perm.user_mode,
            "user_list": perm.user_list,
        },
    }


# ==================== 虚拟 Agent 模块（框架级注入，不依赖模块目录） ====================

VIRTUAL_AGENT_MODULE = "agent"


def _get_agent_runtime(container, bot_id):
    from app.llm.manager import AgentManager

    manager = container.get(AgentManager)
    if manager is None or bot_id is None:
        return None
    return manager.get_runtime(bot_id)


class _AgentAuthority:
    """虚拟 Agent 模块的权限代理：读写框架 AgentConfig 权限。"""

    def __init__(self, runtime):
        self._rt = runtime

    @property
    def enabled(self):
        return self._rt.config.enabled

    def set_enabled(self, value):
        self._rt.config.set_enabled(value)

    @property
    def permission(self):
        return self._rt.config.permission

    def update_permission(self, group_mode, group_list, user_mode, user_list):
        self._rt.config.update_permission(group_mode, group_list, user_mode, user_list)


class _AgentProxy:
    """虚拟 Agent 模块：config / authority 代理到框架运行时。"""

    name = "LLM服务"
    sign = "Agent"
    module_name = VIRTUAL_AGENT_MODULE

    def __init__(self, runtime):
        from app.llm.config_schema import SCHEMA

        self.bot_id = runtime.bot_id
        self.config = runtime.config
        # 复制一份 schema 并注入 Preset 选项，使模块配置页/Agent 表单共用
        self.config_schema = dict(SCHEMA)
        self.authority = _AgentAuthority(runtime)


def _resolve_module(container, module_name, bot_id):
    """按名称解析模块：'agent' 为框架虚拟模块（读运行时），否则查注册表。"""
    if module_name == VIRTUAL_AGENT_MODULE:
        runtime = _get_agent_runtime(container, bot_id)
        if runtime is None:
            return None
        return _AgentProxy(runtime)
    from app.modules.registry import ModuleRegistry

    return container.get(ModuleRegistry).get(module_name, bot_id)


async def _sync_module_runtime(module, enabled: bool) -> None:
    """禁用/启用时联动模块生命周期，避免「关闭了但定时任务/后台任务还在跑」。

    - 禁用：执行 on_unload → 取消该模块后台任务（owner=module:<name>:<bot>）→ 注销其定时任务；
    - 启用：重新注册模块定时任务并执行 on_load（恢复动态每日任务等）。
    仅对真实模块实例生效（agent 等虚拟模块走运行时开关，不在此列）。
    """
    if module is None or getattr(module, "module_name", None) is None:
        return
    module_name = module.module_name
    bot_id = getattr(module, "bot_id", None)
    services = getattr(getattr(module, "ctx", None), "services", None)

    if enabled:
        scheduler = getattr(services, "scheduler", None) if services else None
        if scheduler is not None and bot_id is not None:
            try:
                await scheduler.register_module(module)
            except Exception as e:
                logger.warning(f"[Module] {module_name} 启用后定时任务注册异常: {e}")
        on_load = getattr(module, "on_load", None)
        if on_load is not None:
            try:
                await on_load()
            except Exception as e:
                logger.warning(f"[Module] {module_name} on_load 异常: {e}")
        try:
            if services is not None and getattr(services, "features", None) is not None:
                services.features.acquire_module(module)
        except Exception as e:
            logger.warning(f"[Module] {module_name} 启用后 Feature 接管异常: {e}")
        return

    try:
        on_unload = getattr(module, "on_unload", None)
        if on_unload is not None:
            await on_unload()
    except Exception as e:
        logger.warning(f"[Module] {module_name} on_unload 异常: {e}")

    try:
        if services is not None and getattr(services, "features", None) is not None:
            services.features.release_module(module)
    except Exception as e:
        logger.warning(f"[Module] {module_name} 禁用后 Feature 释放异常: {e}")

    if services is not None and bot_id is not None:
        task_manager = getattr(services, "task_manager", None)
        if task_manager is not None:
            try:
                task_manager.cancel_owner(f"module:{module_name}:{bot_id}")
            except Exception as e:
                logger.warning(f"[Module] {module_name} 后台任务取消异常: {e}")
        scheduler = getattr(services, "scheduler", None)
        if scheduler is not None:
            try:
                await scheduler.unload_module(module_name, bot_id)
            except Exception as e:
                logger.warning(f"[Module] {module_name} 定时任务注销异常: {e}")


@router.get("/modules")
async def api_modules(request: Request, bot_id: int | None = Depends(parse_bot_id)):
    from app.services.bot_service import BotService

    container = get_container(request)
    return JSONResponse(content=container.get(BotService).get_modules_data(bot_id))


@router.get("/modules/uninstalled")
async def list_uninstalled_modules(request: Request):
    """返回软卸载模块清单（设置页面弹窗用）。"""
    container = get_container(request)
    install_service = container.get(ModuleInstallService)
    return JSONResponse(content={"ok": True, "modules": install_service.list_uninstalled()})


@router.get("/modules/features")
async def list_features(request: Request, bot_id: int | None = Depends(parse_bot_id)):
    from app.modules.features import FeatureRegistry

    container = get_container(request)
    features = container.get(FeatureRegistry).status(bot_id)
    return JSONResponse(content={"ok": True, "features": features})


@router.get("/modules/{module_name}")
async def api_module(module_name: str, request: Request, bot_id: int | None = Depends(parse_bot_id)):
    from app.services.bot_service import BotService

    container = get_container(request)
    data = container.get(BotService).get_modules_data(bot_id).get(module_name)
    if data is None:
        return _err(404, f"Module {module_name} not found")
    return JSONResponse(content={module_name: data})


@router.post("/module/{module_name}/toggle")
async def toggle_module(module_name: str, request: Request,
                        bot_id: int | None = Depends(parse_bot_id), enabled: bool = Form(...)):
    container = get_container(request)
    module = _resolve_module(container, module_name, bot_id)
    if not module:
        return _err(404, f"模块 {module_name} (Bot {bot_id}) 不存在")
    module.authority.set_enabled(enabled)
    # 生命周期联动：禁用 -> 停止定时任务/取消后台任务；启用 -> 恢复定时任务（含动态每日任务）
    await _sync_module_runtime(module, enabled)
    await manager.broadcast(json.dumps(_authority_payload(module, bot_id, enabled)))
    return _ok(f"模块 {module.name} (Bot {bot_id}) 已{'启用' if enabled else '禁用'}")


@router.post("/module/{module_name}/permission")
async def update_permission(
    module_name: str, request: Request, bot_id: int | None = Depends(parse_bot_id),
    group_mode: str = Form(...), group_list: str = Form(""),
    user_mode: str = Form(...), user_list: str = Form(""),
):
    container = get_container(request)
    if not bot_id:
        return _err(404, f"模块 {module_name} 无 Bot ID 实例")
    module = _resolve_module(container, module_name, bot_id)
    if not module:
        return _err(404, f"模块 {module_name} (Bot {bot_id}) 不存在")

    g_ids = [g.strip() for g in group_list.split("\n") if g.strip()]
    u_ids = [u.strip() for u in user_list.split("\n") if u.strip()]
    module.authority.update_permission(group_mode, g_ids, user_mode, u_ids)

    await manager.broadcast(json.dumps(_authority_payload(module, bot_id, module.authority.enabled)))
    return _ok(f"模块 {module.name} (Bot {bot_id}) 权限已更新")


@router.get("/module/{module_name}/config")
async def get_module_config(module_name: str, request: Request,
                            bot_id: int | None = Depends(parse_bot_id)):
    """读取模块配置（自定义配置页用）。返回已脱敏（password 打码）的配置。"""
    from app.services.bot_service import BotService

    container = get_container(request)
    data = container.get(BotService).get_modules_data(bot_id).get(module_name)
    if data is None:
        return _err(404, f"模块 {module_name} 不存在")
    return JSONResponse(content={"ok": True, "module": module_name, "bot_id": bot_id, "config": data["config"]})


@router.post("/module/{module_name}/config")
async def update_config(module_name: str, request: Request, bot_id: int | None = Depends(parse_bot_id)):
    container = get_container(request)
    if not bot_id:
        return _err(404, f"模块 {module_name} 无 Bot ID 实例")
    module = _resolve_module(container, module_name, bot_id)
    if not module:
        return _err(404, f"模块 {module_name} (Bot {bot_id}) 不存在")

    try:
        data = await request.json()
    except Exception:
        data = {}
    data = data or {}
    for key in LEGACY_LLM_CONNECTION_KEYS:
        data.pop(key, None)
    for key, value in data.items():
        # password 字段值为脱敏哨兵时保留旧值（用户未修改密码）
        field = module.config_schema.get(key)
        if isinstance(field, dict) and field.get("type") == "password" and value == _PASSWORD_MASK:
            continue
        module.config.set(key, value, auto_save=False)
    module.config.save()

    await manager.broadcast(json.dumps({
        "type": "module_config_updated",
        "module": module_name,
        "bot_id": bot_id,
        # 广播前脱敏：password 字段打码，避免明文密码经 WS 泄露（与读取接口一致）
        "config": _mask_password_config(module.config.raw_config, module.config_schema),
    }))
    return _ok(f"模块 {module.name} (Bot {bot_id}) 配置已更新")


@router.post("/modules/reload")
async def reload_modules(request: Request, bot_id: int | None = Depends(parse_bot_id)):
    from app.services.bot_service import BotService

    container = get_container(request)
    bot_service = container.get(BotService)
    bot = bot_service.gateway.find_conn_by_bot_id(bot_id) if bot_id else None
    await bot_service.registry.reload_all(bot_id, bot=bot)
    await manager.broadcast(json.dumps({"type": "modules_reloaded", "bot_id": bot_id}))
    return _ok("模块已重新加载")


@router.post("/modules/{module_name}/reload")
async def reload_single_module(
    module_name: str,
    request: Request,
    bot_id: int | None = Depends(parse_bot_id),
):
    """手动热重载单个模块。"""
    from app.modules.registry import ModuleRegistry
    from app.services.bot_service import BotService

    container = get_container(request)
    bot_service = container.get(BotService)
    registry = container.get(ModuleRegistry)
    bot = bot_service.gateway.find_conn_by_bot_id(bot_id) if bot_id else None
    ok = await registry.reload_single(module_name, bot_id, bot=bot)
    if not ok:
        return _err(404, f"模块 {module_name} (Bot {bot_id}) 重载失败或不存在")
    await manager.broadcast(json.dumps({"type": "modules_reloaded", "bot_id": bot_id}))
    return _ok(f"模块 {module_name} 已重新加载")


@router.post("/modules/{module_name}/uninstall")
async def uninstall_module(
    module_name: str,
    request: Request,
    bot_id: int | None = Depends(parse_bot_id),
):
    """软卸载模块：只写配置文件，不删除模块文件。"""
    from app.modules.registry import ModuleRegistry

    container = get_container(request)
    registry = container.get(ModuleRegistry)
    try:
        await registry.uninstall_module(module_name)
    except ValueError as e:
        return _err(400, str(e))
    await manager.broadcast(json.dumps({"type": "modules_reloaded", "bot_id": bot_id}))
    return _ok(f"模块 {module_name} 已软卸载（文件保留）")


@router.post("/modules/{module_name}/install")
async def reinstall_module(
    module_name: str,
    request: Request,
    bot_id: int | None = Depends(parse_bot_id),
):
    """清除软卸载记录并重新加载模块。"""
    from app.modules.registry import ModuleRegistry
    from app.services.bot_service import BotService

    container = get_container(request)
    bot_service = container.get(BotService)
    registry = container.get(ModuleRegistry)
    bot = bot_service.gateway.find_conn_by_bot_id(bot_id) if bot_id else None
    ok = await registry.reinstall_module(module_name, bot_id)
    if not ok:
        return _err(404, f"模块 {module_name} 目录不存在或安装失败")
    await manager.broadcast(json.dumps({"type": "modules_reloaded", "bot_id": bot_id}))
    return _ok(f"模块 {module_name} 已恢复安装")


@router.post("/modules/install-zip")
async def install_module_zip(request: Request, file: UploadFile = File(...)):
    """上传 zip 插件并安装到 module/plugins。"""
    from app.modules.registry import ModuleRegistry

    container = get_container(request)
    registry = container.get(ModuleRegistry)
    fd, tmp_path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    try:
        with open(tmp_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
        info = await registry.install_from_zip(tmp_path)
    except ValueError as e:
        return _err(400, str(e))
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    await manager.broadcast(json.dumps({"type": "modules_reloaded", "bot_id": None}))
    return _ok("插件安装成功", plugin=info)


# ==================== 自定义配置页 ====================

@router.get("/module/{module_name}/page", response_class=HTMLResponse)
async def module_page(module_name: str, request: Request,
                      bot_id: int | None = Depends(parse_bot_id)):
    """自定义配置页（仅模块目录 pages/index.html；框架级 Agent 已并入 schema 表单，无独立页）。"""
    container = get_container(request)
    from app.modules.registry import ModuleRegistry

    reg = container.get(ModuleRegistry)
    page_path = reg.module_page_path(module_name)
    if page_path is None:
        return _err(404, f"模块 {module_name} 无自定义页面")
    content = page_path.read_text(encoding="utf-8")

    # 注入模块名 / 当前选中账号 / WebUI 访问令牌，方便页面 JS 拼接配置 API 并携带鉴权
    from app.core.settings import Settings

    webui_token = container.get(Settings).webui_token or ""
    module_var = (
        f'<script>window.PLUGIN_MODULE = {json.dumps(module_name)};'
        f'window.PLUGIN_BOT_ID = {json.dumps(bot_id) if bot_id is not None else "null"};'
        f'window.WEBUI_TOKEN = {json.dumps(webui_token)};</script>'
    )
    if "<head>" in content:
        content = content.replace("<head>", f"<head>{module_var}", 1)
    else:
        content = module_var + content
    return HTMLResponse(content=content)




# ==================== list / dynamic 数据源 ====================

def _find_schema_field(module, field_type: str, endpoint: str) -> dict | None:
    """在模块 config_schema 中查找 type 与 endpoint 匹配的字段定义（返回副本，不污染类级 schema）。"""
    schema = getattr(module, "config_schema", None) or {}
    for key, field in schema.items():
        if not isinstance(field, dict):
            continue
        if field.get("type") == field_type and field.get("endpoint") == endpoint:
            result = dict(field)
            result["key"] = key
            return result
    return None


def _resolve_bot(container, bot_id: int | None):
    if not bot_id:
        return None
    from app.infrastructure.onebot.gateway import OneBotGateway

    return container.get(OneBotGateway).find_conn_by_bot_id(bot_id)


@router.get("/module/{module_name}/list/{endpoint}")
async def module_list_data(module_name: str, endpoint: str, request: Request,
                           bot_id: int | None = Depends(parse_bot_id)):
    from app.modules.registry import ModuleRegistry
    from app.services.provider_service import ProviderRegistry

    container = get_container(request)
    module = container.get(ModuleRegistry).get(module_name, bot_id)
    if not module:
        return _err(404, f"模块 {module_name} (Bot {bot_id}) 不存在")
    field = _find_schema_field(module, "list", endpoint)
    if field is None:
        return _err(404, f"模块 {module_name} 无 list 字段 endpoint={endpoint}")

    bot = _resolve_bot(container, bot_id)
    try:
        data = await container.get(ProviderRegistry).call(
            module.module_name, endpoint, "list", module, bot, field
        )
    except Exception as e:
        return _err(502, f"数据源请求失败: {e}")
    items = data.get("items") or data.get("groups") or data.get("friends") or []

    # 合并已存配置：{<id>: {enabled, index}} → 每项回填 enabled/index，并排序
    saved = module.config.get(field["key"], {}) or {}
    id_field = field.get("id_field", "id")
    name_field = field.get("name_field", "name")
    meta_fields = field.get("meta_fields", []) or []
    normalized = []
    for i, item in enumerate(items):
        iid = str(item.get(id_field, ""))
        cfg = saved.get(iid, {}) if isinstance(saved, dict) else {}
        normalized.append({
            "id": iid,
            "name": item.get(name_field, "") or iid,
            "meta": [item.get(mf) for mf in meta_fields],
            "enabled": bool(cfg.get("enabled", item.get("enabled", True))),
            "index": cfg.get("index", i),
        })
    normalized.sort(key=lambda x: x["index"])
    mode = module.config.get(field["key"] + "_mode", "all")
    return JSONResponse(content={"ok": True, "items": normalized, "mode": mode})


@router.get("/module/{module_name}/dynamic/{endpoint}")
async def module_dynamic_options(module_name: str, endpoint: str, request: Request,
                                 bot_id: int | None = Depends(parse_bot_id)):
    from app.modules.registry import ModuleRegistry
    from app.services.provider_service import ProviderRegistry

    container = get_container(request)
    module = container.get(ModuleRegistry).get(module_name, bot_id)
    if not module:
        return _err(404, f"模块 {module_name} (Bot {bot_id}) 不存在")
    field = _find_schema_field(module, "dynamic", endpoint)
    if field is None:
        return _err(404, f"模块 {module_name} 无 dynamic 字段 endpoint={endpoint}")

    bot = _resolve_bot(container, bot_id)
    try:
        data = await container.get(ProviderRegistry).call(
            module.module_name, endpoint, "dynamic", module, bot, field, value=None
        )
    except Exception as e:
        return _err(502, f"数据源请求失败: {e}")
    return JSONResponse(content={"ok": True, "options": data.get("options", [])})


@router.get("/module/{module_name}/dynamic/{endpoint}/{value}")
async def module_dynamic_fields(module_name: str, endpoint: str, value: str, request: Request,
                                bot_id: int | None = Depends(parse_bot_id)):
    from app.modules.registry import ModuleRegistry
    from app.services.provider_service import ProviderRegistry

    container = get_container(request)
    module = container.get(ModuleRegistry).get(module_name, bot_id)
    if not module:
        return _err(404, f"模块 {module_name} (Bot {bot_id}) 不存在")
    field = _find_schema_field(module, "dynamic", endpoint)
    if field is None:
        return _err(404, f"模块 {module_name} 无 dynamic 字段 endpoint={endpoint}")

    bot = _resolve_bot(container, bot_id)
    try:
        data = await container.get(ProviderRegistry).call(
            module.module_name, endpoint, "dynamic", module, bot, field, value=value
        )
    except Exception as e:
        return _err(502, f"数据源请求失败: {e}")
    return JSONResponse(content={"ok": True, "fields": data.get("fields", [])})
