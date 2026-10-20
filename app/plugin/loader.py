"""插件加载的各道工序：加载 = 装配 → 绑钩子 → 连数据源 → 激活。

## 为什么拆出来

`ModuleRegistry.load_single` 原本 234 行，一个方法里塞了六件事：解析校验、导入类、
实例化、绑装饰器钩子、往六七个钩子注册表登记、挂数据源与定时任务、调 `on_load`、
递归子模块。改「新增一类钩子」要在这 234 行里找到第 N 段几乎相同的代码抄一遍。

拆开后 `load_single` 只剩编排（~50 行），每道工序一个函数：

    instantiate_module   导入 Module 类并实例化，设好通用属性
    bind_module_hooks    @module_hook 绑到实例上并推导 subscribe
    register_hooks       表驱动地把 7 类钩子登记到各自注册表
    register_data_sources  声明式数据源（list/dynamic）+ 定时任务 + 依赖自检
    activate             on_load + 能力接管

## 表驱动的好处

原先 send / before_send / api / lifecycle / event_completed 五段是逐字复制的
`for hook in ...: handler = getattr(...); if None: continue; servicereg.register(...)`。
现在只在 `_SERVICE_HOOKS` 里加一行。加第六类钩子不需要再抄一遍循环。
"""

from __future__ import annotations

from typing import Any

from app.domain.bot import IBot
from app.plugin.base import BaseModule, ModuleAuthority, ModuleConfig, ModuleContext, ServiceAccess

#: (类上的收集方法, services 上的注册表字段, 分派键或 None, 日志用名称)
#: 分派键为 None 表示该注册表的 `register()` 不接受额外维度。
_SERVICE_HOOKS: tuple[tuple[str, str, str | None, str], ...] = (
    ("collect_send_hooks", "send_hooks", "message_type", "发送成功"),
    ("collect_before_send_hooks", "before_send_hooks", "message_type", "发送前"),
    ("collect_api_hooks", "api_hooks", "action", "API 调用后"),
    ("collect_lifecycle_hooks", "lifecycle_hooks", "state", "Bot 生命周期"),
    ("collect_event_completed_hooks", "event_completed_hooks", None, "事件处理完成"),
)


def instantiate_module(
    *,
    module_name: str,
    module_path,
    source: str,
    bot_id: Any,
    bot: IBot | None,
    parent: BaseModule | None,
    config_service,
    services: ServiceAccess,
    read_manifest,
    log,
) -> BaseModule | None:
    """导入插件模块并实例化；类不合格时记日志并返回 None（不抛）。"""
    from app.plugin.registry import ModuleRegistry

    ModuleRegistry._purge_module_cache(module_name)
    mod = ModuleRegistry._import_plugin_module(module_name, module_path)
    cls = getattr(mod, "Module", None)
    if cls is None:
        log.error(f"[Module] {module_name} 缺少 module.py 中的 Module 类")
        return None
    if not isinstance(cls, type) or not issubclass(cls, BaseModule):
        log.error(f"[Module] {module_name} 的 Module 类未继承 BaseModule")
        return None

    ctx = ModuleContext(
        module_name=module_name,
        bot_id=bot_id,
        config=ModuleConfig(module_name, bot_id, cls.default_config, config_service),
        authority=ModuleAuthority(module_name, bot_id, config_service),
        services=services,
        bot=bot,
    )
    instance = cls(ctx)
    instance.module_name = module_name
    instance.bot_id = bot_id
    instance.parent = parent          # 父模块引用（None = 顶层模块）
    instance.children = {}            # 子模块：{短名: 实例}
    instance.source = source
    instance.version = str(read_manifest(module_path).get("version", "") or "") or "0.0.0"
    instance.plugin_dir = str(module_path)
    instance.can_uninstall = True     # 本地/外部插件都允许软卸载（文件保留）
    return instance


def bind_module_hooks(instance: BaseModule, cls: type) -> tuple[list[dict], list[dict]]:
    """把 `@module_hook` 绑到实例上；返回 (module_hooks, llm_hooks)。

    `BaseModule.collect_hooks()` 一次返回两族钩子的声明，这里只绑 module 那族，
    llm 那族原样交回给 `register_hooks` 去登记（它们挂在 runtime 上）。
    """
    module_hooks, llm_hooks = cls.collect_hooks()
    instance._module_hooks = []
    for hook in module_hooks:
        handler = getattr(instance, hook["method"], None)
        if handler is None:
            continue
        instance._module_hooks.append({
            "event_type": hook.get("event_type", "*"),
            "order": hook.get("order", 100),
            "handler": handler,
        })
    # 未显式声明 subscribe 时，从 @module_hook 自动推导（"*" 表示全部）
    if not getattr(cls, "subscribe", ()):
        instance.subscribe = tuple({h["event_type"] for h in module_hooks})
    return module_hooks, llm_hooks


def _register_service_hooks(instance: BaseModule, cls: type, bot_id: Any, services: ServiceAccess, log) -> None:
    """表驱动：把 5 类服务钩子登记到各自的注册表（加新类只需在 `_SERVICE_HOOKS` 加一行）。"""
    for collector, registry_attr, dispatch_key, label in _SERVICE_HOOKS:
        hooks = getattr(cls, collector)()
        registry = getattr(services, registry_attr, None) if services else None
        if not hooks or registry is None:
            continue
        for hook in hooks:
            handler = getattr(instance, hook["method"], None)
            if handler is None:
                continue
            kwargs = {"bot_id": bot_id, "module": instance, "handler": handler,
                      "order": hook.get("order", 100)}
            if dispatch_key is not None:
                kwargs[dispatch_key] = hook.get(dispatch_key, "*")
            registry.register(**kwargs)


def _register_llm_hooks(instance: BaseModule, llm_hooks: list[dict], bot_id: Any, services: ServiceAccess) -> None:
    """LLM 流水线钩子（挂在 runtime 上，不在 services 上）。"""
    if not llm_hooks or services is None or services.agent_manager is None:
        return
    runtime = services.agent_manager.get_runtime(bot_id)
    if runtime is None or not hasattr(runtime, "llm_hooks"):
        return
    for hook in llm_hooks:
        method = hook.get("method")
        handler = hook.get("handler")
        if method:
            handler = getattr(instance, method, None)
        elif isinstance(handler, str):
            handler = getattr(instance, handler, None)
        if handler is None or not callable(handler):
            continue
        runtime.llm_hooks.register(
            stage=hook.get("stage", ""),
            event_type=hook.get("event_type", "*"),
            order=hook.get("order", 100),
            handler=handler,
            module=instance,
        )


def _register_runtime_capabilities(instance: BaseModule, cls: type, bot_id: Any, services: ServiceAccess) -> None:
    """模块工具 / 技能 / 工具调用后钩子（都挂在 runtime 上）。"""
    if services is None or services.agent_manager is None:
        return
    runtime = services.agent_manager.get_runtime(bot_id)
    if runtime is None:
        return
    runtime.llm_tools.register_module(instance)
    runtime.skills.register_module(instance)
    tool_call_hooks = cls.collect_tool_call_hooks()
    if tool_call_hooks and hasattr(runtime, "llm_tool_call_hooks"):
        for hook in tool_call_hooks:
            handler = getattr(instance, hook["method"], None)
            if handler is None:
                continue
            runtime.llm_tool_call_hooks.register(
                event_type=hook.get("event_type", "*"),
                order=hook.get("order", 100),
                handler=handler,
                module=instance,
            )


def register_hooks(
    instance: BaseModule, cls: type, llm_hooks: list[dict], bot_id: Any,
    services: ServiceAccess, log,
) -> None:
    """把该插件声明的所有钩子登记到框架各处。"""
    _register_service_hooks(instance, cls, bot_id, services, log)
    _register_llm_hooks(instance, llm_hooks, bot_id, services)
    _register_runtime_capabilities(instance, cls, bot_id, services)


async def register_data_sources(instance: BaseModule, module_name: str, services: ServiceAccess, log) -> None:
    """声明式数据源（list/dynamic）+ 定时任务 + `requires` 依赖自检。

    三者都是「声明即接入」，失败只警告不阻断——模块自行降级。
    """
    providers = services.providers if services else None
    if providers is not None:
        try:
            providers.register_module(instance)
        except Exception as e:  # noqa: BLE001
            log.warning(f"[Module] {module_name} 数据源注册异常: {e}")

    scheduler = services.scheduler if services else None
    if scheduler is not None:
        try:
            await scheduler.register_module(instance)
        except Exception as e:  # noqa: BLE001
            log.warning(f"[Module] {module_name} 定时任务注册异常: {e}")

    try:
        missing = instance.missing_requirements()
        if missing:
            log.warning(f"[Module] {module_name} 声明依赖缺失: {', '.join(missing)}（相关功能会降级）")
    except Exception as e:  # noqa: BLE001 —— 自检失败不该影响模块加载
        log.debug(f"[Module] {module_name} 依赖自检失败（已忽略）: {e}")


async def activate(instance: BaseModule, module_name: str, services: ServiceAccess, log) -> None:
    """`on_load` + 能力接管（`supersedes`）。"""
    try:
        await instance.on_load()
    except Exception as e:  # noqa: BLE001 —— 插件自身异常不能拖垮整个加载流程
        log.warning(f"[Module] {module_name} on_load 异常: {e}")

    try:
        enabled = getattr(getattr(instance, "authority", None), "enabled", True)
        if services and services.features and enabled:
            services.features.acquire_module(instance)
    except Exception as e:  # noqa: BLE001
        log.warning(f"[Module] {module_name} Feature 接管异常: {e}")
