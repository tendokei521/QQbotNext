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

import importlib
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.domain.bot import IBot
from app.plugin.base import (
    BaseModule,
    ModuleAuthority,
    ModuleConfig,
    ModuleContext,
    ServiceAccess,
)
from app.plugin.commands import parse_signature

#: 插件入口文件名（v3.1 起统一为 `main.py`）
ENTRY_FILE = "main.py"
#: 旧入口名，仍兼容：外部 zip 插件可能还是 `module.py`
LEGACY_ENTRY_FILE = "module.py"


def resolve_entry_file(module_path: Path) -> Path | None:
    """返回插件入口文件：优先 `main.py`，回退 `module.py`（兼容旧插件）。"""
    for name in (ENTRY_FILE, LEGACY_ENTRY_FILE):
        candidate = module_path / name
        if candidate.is_file():
            return candidate
    return None


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
    purge_module_cache(module_name)
    mod = import_plugin_module(module_name, module_path)
    cls = getattr(mod, "Module", None)
    if cls is None:
        log.error(f"[Module] {module_name} 的入口文件里缺少 Module 类（应为 main.py）")
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


def bind_commands(instance: BaseModule, cls: type, log=None) -> list[tuple[Any, Any]]:
    """把 `@command` / `@command_group` 声明的命令绑到实例上。

    返回 ``[(CommandSpec, 处理器), ...]``，同时写进 ``instance._commands``。
    命令节点只遍历「本轮被权限放行」的模块的这份清单——不建全局注册表，
    热重载/卸载天然跟着实例走。
    """
    entries: list[tuple[Any, Any]] = []
    for spec, method in cls.collect_commands():
        handler = getattr(instance, method, None)
        if handler is None or not callable(handler):
            continue
        # 签名解析放在加载期：运行期只做类型转换，不重复走 inspect
        entries.append((replace(spec, params=parse_signature(handler)), handler))
    instance._commands = entries
    if entries:
        _derive_subscribe_for_commands(instance, cls)
    if entries and log is not None:
        # 命令是对外契约：加载时登记一条（debug 级，便于核对"这个 bot 有哪些命令"）
        names = ", ".join(sorted({spec.name for spec, _ in entries}))
        log.debug(f"[Module] {getattr(instance, 'module_name', '?')} 命令: {names}")
    return entries


def _derive_subscribe_for_commands(instance: BaseModule, cls: type) -> None:
    """仅靠 ``@command`` 声明能力的模块也要被路由到。

    ``ModuleRouterNode`` 按 ``module.subscribe`` 过滤；``bind_module_hooks`` 只从
    ``@module_hook`` 推导事件类型。命令天然只作用于消息事件，这里补上两个消息类型
    ——否则「只声明了命令、没写 @module_hook」的模块永远收不到事件（命令永不触发）。
    显式写了 ``subscribe`` 的模块以作者声明为准。
    """
    if getattr(cls, "subscribe", ()):
        return
    types = set(getattr(instance, "subscribe", ()) or ())
    types.update({"message_group", "message_private"})
    instance.subscribe = tuple(sorted(types))


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


def import_plugin_module(module_name: str, module_path: Path):
    """按**文件路径**加载插件包，返回它的入口模块（`main.py`，兼容旧 `module.py`）。

    为什么不用 `importlib.import_module("app.plugins.X.module")`：
    那要求插件必须是某个固定包名的子包（历史上是 `module.modules`），于是
    插件放哪儿、测试夹具放哪儿都被这个字符串焊死。改成按路径加载后，插件的
    物理位置与 import 名解耦，写进 `sys.modules` 的是合成包名
    `qqbot_plugin_<name>`。

    仍以「包」的形式加载（`submodule_search_locations` 指向插件目录），因为插件
    内部一律用相对导入（`from .service import ...`），必须让它们能解析。

    兼容两种布局：包内有 `__init__.py`（正常情况）与没有（极简插件目录）。
    """
    pkg_name = f"qqbot_plugin_{module_name.replace('.', '_')}"
    init_file = module_path / "__init__.py"
    if init_file.is_file():
        spec = importlib.util.spec_from_file_location(
            pkg_name, init_file, submodule_search_locations=[str(module_path)]
        )
    else:
        entry = resolve_entry_file(module_path)
        if entry is None:
            raise ImportError(f"插件 {module_name} 缺少入口文件（{ENTRY_FILE}）: {module_path}")
        spec = importlib.util.spec_from_file_location(
            pkg_name, entry, submodule_search_locations=[str(module_path)]
        )
    if spec is None or spec.loader is None:
        raise ImportError(f"无法为插件 {module_name} 构造加载器：{module_path}")
    pkg = importlib.util.module_from_spec(spec)
    sys.modules[pkg_name] = pkg
    spec.loader.exec_module(pkg)
    if init_file.is_file():
        entry = resolve_entry_file(module_path)
        entry_name = entry.stem if entry is not None else Path(ENTRY_FILE).stem
        return importlib.import_module(f"{pkg_name}.{entry_name}")
    return pkg


def purge_module_cache(module_name: str) -> None:
    pkg_name = f"qqbot_plugin_{module_name.replace('.', '_')}"
    for key in [k for k in sys.modules if k == pkg_name or k.startswith(pkg_name + ".")]:
        del sys.modules[key]
