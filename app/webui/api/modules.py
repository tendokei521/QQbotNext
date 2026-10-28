"""模块管理 API：启停 / 权限 / 配置 / 重载。"""

from __future__ import annotations

import asyncio
import json
from zipfile import BadZipFile

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from app.agent.config import LEGACY_LLM_CONNECTION_KEYS
from app.application.bot_service import PASSWORD_MASK as _PASSWORD_MASK
from app.application.bot_service import _mask_password_config
from app.core.logger import logger
from app.webui.api.deps import (
    get_agent_manager,
    get_bot_service,
    get_config_service,
    get_feature_registry,
    get_gateway,
    get_module_install_service,
    get_module_registry,
    get_provider_registry,
    get_resource_registry,
    get_settings,
    parse_account_id,
)
from app.webui.api.module_lookup import (
    _INSTALL_ERROR_HINT,
    _catalog_entry,
    _entry_is_builtin,
    _install_error,
    _module_dir_exists,
    _module_exists,
    _uploaded_zip,
)
from app.webui.ws import manager

router = APIRouter(tags=["modules"])


def _ok(message: str, **extra):
    return JSONResponse(content={"status": "success", "message": message, **extra})


def _err(status: int, message: str, hint: str = ""):
    body = {"status": "error", "message": message}
    if hint:
        body["hint"] = hint
    return JSONResponse(status_code=status, content=body)


def _authority_payload(module, bot_id, enabled):
    perm = module.authority.permission
    return {
        "type": "module_authority_updated",
        "module": module.module_name,
        # bot_id 为历史字段，account_id 为正式名：同时下发，旧前端/新前端都能正确过滤
        "bot_id": bot_id,
        "account_id": bot_id,
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


def _get_agent_runtime(agent_manager, bot_id):
    """取该账号的 Agent 运行时；**账号存在但还没连上时按需装配**。

    配置类接口不该要求连接：账号 id 在创建时就有（v4），装配出来的运行时 ``online=False``，
    只提供配置读写能力。账号真的不存在时才返回 None（调用方回 404）。
    """
    manager = agent_manager
    if manager is None or bot_id is None:
        return None
    runtime = manager.get_runtime(bot_id)
    if runtime is not None:
        return runtime
    config_service = getattr(manager, "config_service", None)
    if config_service is not None:
        try:
            if not any(str(b.get("id")) == str(bot_id) for b in config_service.get_bots()):
                return None
        except Exception:  # noqa: BLE001 —— 配置读失败按不存在处理
            return None
    ensure = getattr(manager, "ensure_runtime", None) or getattr(manager, "bind_account", None)
    return ensure(bot_id) if callable(ensure) else None


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
    #: 框架级 Agent 恒声明支持单一服务；配置键就是 WebUI 存的 "agent"
    supports_single_service = True
    single_service_key = VIRTUAL_AGENT_MODULE

    def __init__(self, runtime):
        from app.agent.config_schema import SCHEMA

        self.bot_id = runtime.bot_id
        self.config = runtime.config
        # 复制一份 schema 并注入 Preset 选项，使模块配置页/Agent 表单共用
        self.config_schema = dict(SCHEMA)
        self.authority = _AgentAuthority(runtime)


def _resolve_module(agent_manager, registry, module_name, bot_id):
    """按名称解析模块：'agent' 为框架虚拟模块（读运行时），否则查注册表。"""
    if module_name == VIRTUAL_AGENT_MODULE:
        runtime = _get_agent_runtime(agent_manager, bot_id)
        if runtime is None:
            return None
        return _AgentProxy(runtime)
    return registry.get(module_name, bot_id)


def _account_online(module) -> bool:
    """模块所属账号当前是否在线（取 AgentRuntime.online；取不到按离线处理）。"""
    services = getattr(getattr(module, "ctx", None), "services", None)
    getter = getattr(services, "runtime", None)
    if not callable(getter):
        return False
    try:
        runtime = getter(getattr(module, "bot_id", None))
    except Exception:  # noqa: BLE001 —— 取运行时失败按离线处理，不影响配置保存
        return False
    return bool(getattr(runtime, "online", False))


async def _sync_module_runtime(module, enabled: bool) -> None:
    """禁用/启用时联动模块生命周期，避免「关闭了但定时任务/后台任务还在跑」。

    - 禁用：执行 on_unload → 取消该模块后台任务（owner=module:<name>:<bot>）→ 注销其定时任务；
    - 启用：重新注册模块定时任务并执行 on_load（恢复动态每日任务等）；
      账号在线时补调 on_online（否则「在线启用」后计时器/主动消息要等下次重连才武装）。
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
        # 已在线时启用：补一次 on_online，与「账号上线」路径对齐
        if _account_online(module):
            on_online = getattr(module, "on_online", None)
            if on_online is not None:
                try:
                    await on_online()
                except Exception as e:
                    logger.warning(f"[Module] {module_name} 启用后 on_online 异常: {e}")
        try:
            if services is not None and getattr(services, "features", None) is not None:
                services.features.acquire_module(module)
        except Exception as e:
            logger.warning(f"[Module] {module_name} 启用后 Feature 接管异常: {e}")
        return

    try:
        on_offline = getattr(module, "on_offline", None)
        if on_offline is not None:
            await on_offline()
    except Exception as e:
        logger.warning(f"[Module] {module_name} 禁用时 on_offline 异常: {e}")

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


async def _with_module_origins(data: dict, config_service) -> dict:
    """给模块列表补「来自预设 xxx」来源（台账不可用时静默跳过，不影响模块页）。"""
    if not data:
        return data
    try:
        from app.application.bundle.origins import load_origins

        origins = await load_origins(config_service.db)
    except Exception as e:  # noqa: BLE001 —— 来源只是展示信息，不能拖垮模块页
        logger.warning(f"[Module] 读取模块来源台账失败（已忽略）: {e}")
        return data
    for name, origin in origins.items():
        if name in data:
            data[name]["origin"] = origin
    return data


@router.get("/modules")
async def api_modules(
    request: Request,
    bot_id: str | None = Depends(parse_account_id),
    bot_service=Depends(get_bot_service),
    config_service=Depends(get_config_service),
):

    data = await _with_module_origins(bot_service.get_modules_data(bot_id), config_service)
    return JSONResponse(content=data)


@router.get("/modules/uninstalled")
async def list_uninstalled_modules(request: Request, install_service=Depends(get_module_install_service)):
    """返回软卸载模块清单（设置页面弹窗用）。"""
    install_service = install_service
    return JSONResponse(content={"ok": True, "modules": install_service.list_uninstalled()})


@router.get("/modules/features")
async def list_features(request: Request, bot_id: str | None = Depends(parse_account_id), features=Depends(get_feature_registry)):

    features = features.status(bot_id)
    return JSONResponse(content={"ok": True, "features": features})


@router.get("/modules/{module_name}")
async def api_module(
    module_name: str,
    request: Request,
    bot_id: str | None = Depends(parse_account_id),
    bot_service=Depends(get_bot_service),
    config_service=Depends(get_config_service),
):

    data = bot_service.get_modules_data(bot_id).get(module_name)
    if data is None:
        return _err(404, f"Module {module_name} not found")
    data = (await _with_module_origins({module_name: data}, config_service))[module_name]
    return JSONResponse(content={module_name: data})


@router.post("/module/{module_name}/toggle")
async def toggle_module(module_name: str, request: Request,
                        bot_id: str | None = Depends(parse_account_id), enabled: bool = Form(...), agent_manager=Depends(get_agent_manager), registry=Depends(get_module_registry)):
    module = _resolve_module(agent_manager, registry, module_name, bot_id)
    if not module:
        return _err(404, f"模块 {module_name} (Bot {bot_id}) 不存在")
    module.authority.set_enabled(enabled)
    # 生命周期联动：禁用 -> 停止定时任务/取消后台任务；启用 -> 恢复定时任务（含动态每日任务）
    await _sync_module_runtime(module, enabled)
    await manager.broadcast(json.dumps(_authority_payload(module, bot_id, enabled)))
    return _ok(f"模块 {module.name} (Bot {bot_id}) 已{'启用' if enabled else '禁用'}")


@router.post("/module/{module_name}/permission")
async def update_permission(
    module_name: str, request: Request, bot_id: str | None = Depends(parse_account_id),
    group_mode: str = Form(...), group_list: str = Form(""),
    user_mode: str = Form(...), user_list: str = Form(""),
    agent_manager=Depends(get_agent_manager), registry=Depends(get_module_registry)):
    if not bot_id:
        return _err(404, f"模块 {module_name} 无 Bot ID 实例")
    module = _resolve_module(agent_manager, registry, module_name, bot_id)
    if not module:
        return _err(404, f"模块 {module_name} (Bot {bot_id}) 不存在")

    g_ids = [g.strip() for g in group_list.split("\n") if g.strip()]
    u_ids = [u.strip() for u in user_list.split("\n") if u.strip()]
    # 非法模式在判定链里"两个分支都不进" = 放行所有人；保存时直接拒绝
    from app.plugin.value_validator import validate_permission_modes

    mode_issues = validate_permission_modes(group_mode, user_mode)
    if mode_issues:
        return _err(400, "；".join(issue["message"] for issue in mode_issues))
    module.authority.update_permission(group_mode, g_ids, user_mode, u_ids)

    await manager.broadcast(json.dumps(_authority_payload(module, bot_id, module.authority.enabled)))
    return _ok(f"模块 {module.name} (Bot {bot_id}) 权限已更新")


@router.get("/module/{module_name}/config")
async def get_module_config(module_name: str, request: Request,
                            bot_id: str | None = Depends(parse_account_id), bot_service=Depends(get_bot_service)):
    """读取模块配置（自定义配置页用）。返回已脱敏（password 打码）的配置。"""

    data = bot_service.get_modules_data(bot_id).get(module_name)
    if data is None:
        return _err(404, f"模块 {module_name} 不存在")
    return JSONResponse(content={"ok": True, "module": module_name, "bot_id": bot_id, "config": data["config"]})


@router.post("/module/{module_name}/config")
async def update_config(module_name: str, request: Request, bot_id: str | None = Depends(parse_account_id), resource_registry=Depends(get_resource_registry), agent_manager=Depends(get_agent_manager), registry=Depends(get_module_registry)):
    if not bot_id:
        return _err(404, f"模块 {module_name} 无 Bot ID 实例")
    module = _resolve_module(agent_manager, registry, module_name, bot_id)
    if not module:
        return _err(404, f"模块 {module_name} (Bot {bot_id}) 不存在")

    try:
        data = await request.json()
    except Exception:
        data = {}
    data = data or {}
    for key in LEGACY_LLM_CONNECTION_KEYS:
        data.pop(key, None)
    # 资源字段的悬挂引用策略（on_missing）：声明 clear 的字段在引用失效时丢弃该值
    from app.plugin.resource_validation import apply_on_missing

    data, cleared_fields = await apply_on_missing(
        module.config_schema, data, resource_registry
    )
    # 值校验：类型/范围/枚举/数组项（导入的配置写错会变成静默垃圾数据）
    from app.plugin.value_validator import validate_config

    value_issues = validate_config(module.config_schema, data)
    if value_issues:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": "配置值不合法，未保存："
                + "；".join(f"{issue['key']}: {issue['message']}" for issue in value_issues[:5]),
                "details": value_issues,
            },
        )
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
        # bot_id 为历史字段，account_id 为正式名（兼容旧前端，见 🔗1）
        "bot_id": bot_id,
        "account_id": bot_id,
        # 广播前脱敏：password 字段打码，避免明文密码经 WS 泄露（与读取接口一致）
        "config": _mask_password_config(module.config.raw_config, module.config_schema),
    }))
    return _ok(f"模块 {module.name} (Bot {bot_id}) 配置已更新", **(dict(cleared=cleared_fields) if cleared_fields else {}))


@router.post("/modules/reload")
async def reload_modules(request: Request, bot_id: str | None = Depends(parse_account_id), bot_service=Depends(get_bot_service)):

    bot_service = bot_service
    bot = bot_service.gateway.find_conn_by_bot_id(bot_id) if bot_id else None
    await bot_service.registry.reload_all(bot_id, bot=bot)
    await manager.broadcast(json.dumps({"type": "modules_reloaded", "bot_id": bot_id}))
    return _ok("模块已重新加载")


@router.post("/modules/{module_name}/reload")
async def reload_single_module(
    module_name: str,
    request: Request,
    bot_id: str | None = Depends(parse_account_id),
    bot_service=Depends(get_bot_service), registry=Depends(get_module_registry)):
    """手动热重载单个模块。"""

    bot_service = bot_service
    registry = registry
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
    bot_id: str | None = Depends(parse_account_id),
    registry=Depends(get_module_registry)):
    """软卸载模块：只写配置文件，不删除模块文件/配置/数据。"""

    registry = registry
    # 校验存在性：此前任意拼错的名字也能写进 tombstone（P1-1）
    if not _module_exists(registry, module_name):
        return _err(404, f"模块 {module_name} 不存在")
    try:
        await registry.uninstall_module(module_name)
    except ValueError as e:
        return _err(404, str(e))
    await manager.broadcast(json.dumps({"type": "modules_reloaded", "bot_id": bot_id}))
    return _ok(f"模块 {module_name} 已软卸载（文件保留）")


@router.post("/modules/{module_name}/install")
async def reinstall_module(
    module_name: str,
    request: Request,
    bot_id: str | None = Depends(parse_account_id),
    bot_service=Depends(get_bot_service), registry=Depends(get_module_registry)):
    """清除软卸载记录并重新加载模块，并为**所有已存在账号**补齐实例。

    此前只恢复当前账号实例，其它账号会回退到别人的实例（P1-1 的多账号错位）。
    """

    bot_service = bot_service
    registry = registry
    ok = await registry.reinstall_module(module_name, bot_id)
    if not ok:
        return _err(404, f"模块 {module_name} 目录不存在或安装失败")
    # 恢复后补实例：与 zip 安装后补装同一套 helper（幂等）
    instances = 0
    ensure = getattr(bot_service, "ensure_module_instances", None)
    if callable(ensure):
        try:
            instances = await ensure(module_name)
        except Exception as e:  # noqa: BLE001 —— 补实例失败不回滚恢复结果，只记日志
            logger.warning(f"[Module] 恢复 {module_name} 后补齐账号实例失败: {e}")
    await manager.broadcast(json.dumps({"type": "modules_reloaded", "bot_id": bot_id}))
    return _ok(f"模块 {module_name} 已恢复安装", instances=instances)


@router.post("/modules/validate-zip")
async def validate_module_zip(
    request: Request,
    file: UploadFile = File(...),
    registry=Depends(get_module_registry)):
    """dry-run 预检上传的 zip：返回名称/版本/目标目录/是否覆盖，供前端确认后再装。"""

    registry = registry
    try:
        with _uploaded_zip(file) as tmp_path:
            # 预检要解压整个 zip（大包几百 MB）：同步跑会占住事件循环，
            # 这段时间 WS 收发全停，容易被对端判成掉线。
            result = await asyncio.to_thread(registry.validate_zip, tmp_path)
    except (ValueError, BadZipFile, UnicodeDecodeError, OSError) as exc:
        return _install_error(exc)
    result = result or {}
    if result.get("ok") is False:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": result.get("message") or result.get("error") or "插件预检未通过",
                "hint": result.get("hint") or _INSTALL_ERROR_HINT,
            },
        )
    plugin = result.get("plugin")
    if not isinstance(plugin, dict):
        # 兼容后端直接返回扁平字段的写法
        plugin = {k: v for k, v in result.items() if k not in ("ok", "hint")}
    extra = {"hint": result["hint"]} if result.get("hint") else {}
    return JSONResponse(content={"ok": True, "plugin": plugin, **extra})


@router.post("/modules/{module_name}/update")
async def update_module_zip(
    module_name: str,
    request: Request,
    file: UploadFile = File(...),
    bot_service=Depends(get_bot_service), registry=Depends(get_module_registry)):
    """覆盖安装（更新）指定模块：等价 install-zip(force=True)，保留配置与数据。"""

    registry = registry
    bot_service = bot_service
    try:
        with _uploaded_zip(file) as tmp_path:
            info = await registry.update_from_zip(tmp_path)
    except (ValueError, BadZipFile, UnicodeDecodeError, OSError) as exc:
        return _install_error(exc)
    info = info or {}
    installed_name = str(info.get("module_name") or info.get("name") or "")
    if installed_name and installed_name != module_name:
        # 上传包与目标不一致时如实告知（不谎报更新成功，避免"升级了另一个插件"）
        message = (
            f"上传包内的插件是「{installed_name}」，已按其自身名称安装；"
            f"与要更新的 {module_name} 不是同一个模块"
        )
    else:
        message = f"插件 {module_name} 已更新"
    ensure = getattr(bot_service, "ensure_module_instances", None)
    if installed_name and installed_name != module_name and callable(ensure):
        try:
            await ensure(installed_name)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[Module] 更新后补齐 {installed_name} 实例失败: {e}")
    await manager.broadcast(json.dumps({"type": "modules_reloaded", "bot_id": None}))
    return _ok(message, plugin=info)


@router.get("/modules/{module_name}/dependencies")
async def module_dependencies(
    module_name: str,
    request: Request,
    registry=Depends(get_module_registry)):
    """只读：查询该已安装插件声明的 pip 依赖与当前缺失清单。"""

    registry = registry
    try:
        status = registry.dependencies_status(module_name)
    except (ValueError, OSError) as exc:
        return _install_error(exc)
    return JSONResponse(content={"ok": True, "status": "success", **status})


@router.post("/modules/{module_name}/install-deps")
async def install_module_dependencies(
    module_name: str,
    request: Request,
    confirm_deps: bool = Form(False),
    mirror: str = Form(""),
    bot_id: str | None = Depends(parse_account_id),
    registry=Depends(get_module_registry)):
    """安装插件声明的 pip 依赖（只装清单里的包名，需前端显式确认）。

    - ``confirm_deps=true`` 必须显式传：装包会改运行环境，不能由"点进页面"触发；
    - 失败不阻断插件本身（返回 502 + stdout/stderr + 可复制命令，插件保持原状）；
    - 成功后重试加载该模块，``loaded`` 告知是否加载成功。
    """

    registry = registry
    if not confirm_deps:
        return _err(
            400,
            "安装依赖需要显式确认",
            "请确认「允许框架用 pip 安装该插件声明的依赖」后再调用（confirm_deps=true）",
        )
    try:
        result = await registry.install_dependencies(module_name, mirror=mirror, bot_id=bot_id)
    except (ValueError, OSError) as exc:
        return _install_error(exc)
    if result.get("ok"):
        await manager.broadcast(json.dumps({"type": "modules_reloaded", "bot_id": bot_id}))
        message = f"插件 {module_name} 的依赖已安装" + ("，模块已重新加载" if result.get("loaded") else "")
        return JSONResponse(content={"ok": True, "status": "success", "message": message, **result})
    hint = "依赖没有装齐；插件保持原状。可复制 command 到终端手动安装后重试"
    return JSONResponse(
        status_code=502,
        content={"ok": False, "status": "error", "message": f"插件 {module_name} 依赖安装失败", "hint": hint, **result},
    )


@router.delete("/modules/{module_name}")
async def delete_module(
    module_name: str,
    request: Request,
    purge_data: bool = False,
    bot_id: str | None = Depends(parse_account_id),
    registry=Depends(get_module_registry)):
    """硬删除**第三方**模块（默认保留配置与数据，`purge_data=true` 才清理数据目录）。

    内置模块不可删除（403）；不存在返回 404；成功广播 modules_reloaded。
    """

    registry = registry
    entry = _catalog_entry(registry, module_name)
    if _entry_is_builtin(entry):
        return _err(403, f"内置模块 {module_name} 不可删除（可禁用或隐藏）")
    if entry is None and not _module_dir_exists(registry, module_name):
        return _err(404, f"模块 {module_name} 不存在")
    try:
        info = await registry.delete_module(module_name, purge_data=purge_data)
    except (ValueError, OSError) as exc:
        # 后端仍判定为内置/不可删时补一个明确提示（避免只有 "xxx" 的裸错误）
        hint = str(getattr(exc, "hint", "") or "")
        return _install_error(exc) if hint else _err(400, str(exc), _INSTALL_ERROR_HINT)
    await manager.broadcast(json.dumps({"type": "modules_reloaded", "bot_id": bot_id}))
    suffix = "（配置与数据已清理）" if purge_data else "（配置与数据已保留）"
    return _ok(f"模块 {module_name} 已删除{suffix}", plugin=info)


@router.post("/modules/install-zip")
async def install_module_zip(
    request: Request,
    file: UploadFile = File(...),
    force: bool = Form(False),
    bot_service=Depends(get_bot_service),
    registry=Depends(get_module_registry)):
    """上传 zip 插件并安装到 data/module/plugins。

    - 已有同名第三方插件时需 `force=true` 才覆盖（后端备份旧版本）；
    - 与内置同名默认拒绝（第三方不可覆盖内置）；
    - 所有已知异常统一 4xx + hint（不再 500）。
    """

    registry = registry
    bot_service = bot_service
    try:
        with _uploaded_zip(file) as tmp_path:
            info = await registry.install_from_zip(tmp_path, force=force)
    except (ValueError, BadZipFile, UnicodeDecodeError, OSError) as exc:
        return _install_error(exc)
    info = info or {}
    name = str(info.get("module_name") or info.get("name") or "")
    # 安装后补实例：后端安装管道通常已补全所有账号，这里幂等兜底（修 P0-5）
    ensure = getattr(bot_service, "ensure_module_instances", None)
    if name and callable(ensure):
        try:
            await ensure(name)
        except Exception as e:  # noqa: BLE001 —— 补实例失败不影响安装结果
            logger.warning(f"[Module] 安装 {name} 后补齐账号实例失败: {e}")
    await manager.broadcast(json.dumps({"type": "modules_reloaded", "bot_id": None}))
    return _ok("插件安装成功", plugin=info)


# ==================== 自定义配置页 ====================

@router.get("/module/{module_name}/page", response_class=HTMLResponse)
async def module_page(module_name: str, request: Request,
                      bot_id: str | None = Depends(parse_account_id), registry=Depends(get_module_registry), settings=Depends(get_settings)):
    """自定义配置页（仅模块目录 pages/index.html；框架级 Agent 已并入 schema 表单，无独立页）。"""

    reg = registry
    page_path = reg.module_page_path(module_name)
    if page_path is None:
        return _err(404, f"模块 {module_name} 无自定义页面")
    content = page_path.read_text(encoding="utf-8")

    # 注入模块名 / 当前选中账号 / WebUI 访问令牌，方便页面 JS 拼接配置 API 并携带鉴权

    webui_token = settings.webui_token or ""
    module_var = (
        f'<script>window.PLUGIN_MODULE = {json.dumps(module_name)};'
        # PLUGIN_ACCOUNT_ID 是正式名（账号 id）；PLUGIN_BOT_ID 保留给既有插件页
        f'window.PLUGIN_ACCOUNT_ID = {json.dumps(bot_id) if bot_id is not None else "null"};'
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


def _resolve_bot(gateway, bot_id: str | None):
    if not bot_id:
        return None

    return gateway.find_conn_by_bot_id(bot_id)


@router.get("/module/{module_name}/list/{endpoint}")
async def module_list_data(module_name: str, endpoint: str, request: Request,
                           bot_id: str | None = Depends(parse_account_id), registry=Depends(get_module_registry), provider_registry=Depends(get_provider_registry), gateway=Depends(get_gateway)):

    module = registry.get(module_name, bot_id)
    if not module:
        return _err(404, f"模块 {module_name} (Bot {bot_id}) 不存在")
    field = _find_schema_field(module, "list", endpoint)
    if field is None:
        return _err(404, f"模块 {module_name} 无 list 字段 endpoint={endpoint}")

    bot = _resolve_bot(gateway, bot_id)
    try:
        data = await provider_registry.call(
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
                                 bot_id: str | None = Depends(parse_account_id), registry=Depends(get_module_registry), provider_registry=Depends(get_provider_registry), gateway=Depends(get_gateway)):

    module = registry.get(module_name, bot_id)
    if not module:
        return _err(404, f"模块 {module_name} (Bot {bot_id}) 不存在")
    field = _find_schema_field(module, "dynamic", endpoint)
    if field is None:
        return _err(404, f"模块 {module_name} 无 dynamic 字段 endpoint={endpoint}")

    bot = _resolve_bot(gateway, bot_id)
    try:
        data = await provider_registry.call(
            module.module_name, endpoint, "dynamic", module, bot, field, value=None
        )
    except Exception as e:
        return _err(502, f"数据源请求失败: {e}")
    return JSONResponse(content={"ok": True, "options": data.get("options", [])})


@router.get("/module/{module_name}/dynamic/{endpoint}/{value}")
async def module_dynamic_fields(module_name: str, endpoint: str, value: str, request: Request,
                                bot_id: str | None = Depends(parse_account_id), registry=Depends(get_module_registry), provider_registry=Depends(get_provider_registry), gateway=Depends(get_gateway)):

    module = registry.get(module_name, bot_id)
    if not module:
        return _err(404, f"模块 {module_name} (Bot {bot_id}) 不存在")
    field = _find_schema_field(module, "dynamic", endpoint)
    if field is None:
        return _err(404, f"模块 {module_name} 无 dynamic 字段 endpoint={endpoint}")

    bot = _resolve_bot(gateway, bot_id)
    try:
        data = await provider_registry.call(
            module.module_name, endpoint, "dynamic", module, bot, field, value=value
        )
    except Exception as e:
        return _err(502, f"数据源请求失败: {e}")
    return JSONResponse(content={"ok": True, "fields": data.get("fields", [])})
