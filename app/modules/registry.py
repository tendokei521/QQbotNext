"""模块注册表：扫描 / 加载 / 热重载 / 卸载业务模块。

替代原 modules/__init__.py 中的 ModuleManager：
- 模块必须位于 <modules_dir>/<name>/module.py，导出 `Module(BaseModule)`；
- 每个模块按 (module_name, bot_id) 实例化，配置/权限来自 ConfigService；
- 热重载时彻底卸载旧实例（含其名下后台任务），再重新导入。
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.core.logger import logger
from app.domain.bot import IBot
from app.modules.base import BaseModule, ModuleAuthority, ModuleConfig, ModuleContext, ServiceAccess


class ModuleRegistry:
    def __init__(
        self,
        *,
        modules_dir: Path | str,
        config_service: Any,
        services: ServiceAccess,
        log=None,
    ) -> None:
        self.modules_dir = Path(modules_dir)
        self.config_service = config_service
        self.services = services
        self.log = log or logger
        self._modules: dict[str, dict[Any, BaseModule]] = {}

    # ---------- 查询 ----------
    def loaded(self) -> list[BaseModule]:
        """全局分发用的模块实例（扁平）。排除子模块——子模块由父模块调度，不参与全局分发。"""
        result = []
        for bot_modules in self._modules.values():
            for module in bot_modules.values():
                if getattr(module, "parent", None) is None:
                    result.append(module)
        return result

    def loaded_map(self) -> dict[str, dict[Any, BaseModule]]:
        return {name: dict(bots) for name, bots in self._modules.items()}

    def get(self, module_name: str, bot_id: Any = None) -> BaseModule | None:
        """取指定 bot 的模块实例；无 bot 专属实例时回退到全局(None)实例。"""
        if module_name not in self._modules:
            return None
        bot_modules = self._modules[module_name]
        if bot_id is None:
            instance = bot_modules.get(None)
            if instance is not None:
                return instance
            return next(iter(bot_modules.values()), None)
        instance = bot_modules.get(bot_id) or bot_modules.get(str(bot_id))
        if instance is None:
            instance = bot_modules.get(None)
        return instance

    def module_names(self) -> list[str]:
        return sorted(self._modules.keys())

    def module_page_path(self, module_name: str) -> Path | None:
        """自定义配置页：module/modules/<name>/pages/index.html。无则返回 None。"""
        page = self._resolve_module_path(module_name) / "pages" / "index.html"
        return page if page.is_file() else None

    def module_has_page(self, module_name: str) -> bool:
        return self.module_page_path(module_name) is not None

    # ---------- 加载 / 卸载 ----------
    def _resolve_module_path(self, module_name: str):
        """解析模块目录路径。点号名（parent.child）→ modules/<parent>/<child>。"""
        parts = module_name.split(".")
        path = self.modules_dir
        for p in parts:
            path = path / p
        return path

    async def load_single(
        self,
        module_name: str,
        bot_id: Any = None,
        bot: IBot | None = None,
        parent: BaseModule | None = None,
    ) -> bool:
        module_path = self._resolve_module_path(module_name)
        if not module_path.is_dir() or module_name.startswith(("_", ".")):
            return False

        try:
            self._purge_module_cache(module_name)
            mod = importlib.import_module(f"module.modules.{module_name}.module")
            cls = getattr(mod, "Module", None)
            if cls is None:
                self.log.error(f"[Module] {module_name} 缺少 module.py 中的 Module 类")
                return False
            if not isinstance(cls, type) or not issubclass(cls, BaseModule):
                self.log.error(f"[Module] {module_name} 的 Module 类未继承 BaseModule")
                return False

            config = ModuleConfig(module_name, bot_id, cls.default_config, self.config_service)
            authority = ModuleAuthority(module_name, bot_id, self.config_service)
            ctx = ModuleContext(
                module_name=module_name,
                bot_id=bot_id,
                config=config,
                authority=authority,
                services=self.services,
                bot=bot,
            )
            instance = cls(ctx)
            instance.module_name = module_name
            instance.bot_id = bot_id
            instance.parent = parent          # 父模块引用（None = 顶层模块）
            instance.children: dict = {}       # 子模块：{短名: 实例}

            # 收集装饰器钩子并绑定到实例
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
                instance.subscribe = tuple({
                    h["event_type"] for h in module_hooks
                })

            self._modules.setdefault(module_name, {})[bot_id] = instance

            # 注册 LLM 流水线钩子到该 Bot 的 AgentRuntime
            if llm_hooks and self.services and self.services.agent_manager:
                runtime = self.services.agent_manager.get_runtime(bot_id)
                if runtime is not None and hasattr(runtime, "llm_hooks"):
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
            # 自动注册模块声明的 list/dynamic 数据源
            providers = self.services.providers if self.services else None
            if providers is not None:
                try:
                    providers.register_module(instance)
                except Exception as e:
                    self.log.warning(f"[Module] {module_name} 数据源注册异常: {e}")
            # 自动注册模块声明的定时任务（仅真实 Bot 实例）
            scheduler = self.services.scheduler if self.services else None
            if scheduler is not None:
                try:
                    await scheduler.register_module(instance)
                except Exception as e:
                    self.log.warning(f"[Module] {module_name} 定时任务注册异常: {e}")
            try:
                await instance.on_load()
            except Exception as e:
                self.log.warning(f"[Module] {module_name} on_load 异常: {e}")

            # 递归加载子模块：父模块目录下的子目录，且含 module.py（忽略 service/ 等普通目录）
            for child_entry in sorted(module_path.iterdir()):
                if not child_entry.is_dir() or child_entry.name.startswith(("_", ".")):
                    continue
                if not (child_entry / "module.py").exists():
                    continue
                child_name = f"{module_name}.{child_entry.name}"
                if bot_id not in self._modules.setdefault(child_name, {}):
                    if await self.load_single(child_name, bot_id, bot, parent=instance):
                        instance.children[child_entry.name] = self._modules[child_name][bot_id]

            #self.log.debug(f"[Module] 加载: {module_name} (bot {bot_id})")
            return True
        except Exception as e:
            self.log.exception(f"[Module] {module_name} (bot {bot_id}) 加载失败: {e}")
            return False

    async def load_all(self, bot_id: Any = None, bot: IBot | None = None) -> int:
        """加载模块目录下所有模块到指定 bot_id。"""
        count = 0
        if not self.modules_dir.exists():
            self.log.warning(f"[Module] 模块目录不存在: {self.modules_dir}")
            return 0
        for entry in sorted(self.modules_dir.iterdir()):
            if not entry.is_dir() or entry.name.startswith(("_", ".")):
                continue
            if entry.name not in self._modules:
                self._modules[entry.name] = {}
            if bot_id not in self._modules[entry.name]:
                if await self.load_single(entry.name, bot_id, bot):
                    count += 1
        self.log.info(f"[Module] Bot {bot_id} 模块加载完成: {count} 个新增")
        return count

    async def unload(self, bot_id: Any = None) -> None:
        """卸载指定 bot_id 的所有模块实例（含 on_unload + 后台任务清理 + 定时任务注销）。"""
        for module_name, bot_modules in list(self._modules.items()):
            instance = bot_modules.pop(bot_id, None)
            if instance is None:
                continue
            self._unregister_llm_hooks(instance, bot_id)
            try:
                await instance.on_unload()
            except Exception as e:
                self.log.warning(f"[Module] {module_name} on_unload 异常: {e}")
            self.services.task_manager.cancel_owner(f"module:{module_name}:{bot_id}")
            scheduler = self.services.scheduler if self.services else None
            if scheduler is not None:
                try:
                    await scheduler.unload_module(module_name, bot_id)
                except Exception as e:
                    self.log.warning(f"[Module] {module_name} 定时任务注销异常: {e}")
            if not bot_modules:
                del self._modules[module_name]

    async def reload_all(self, bot_id: Any = None, bot: IBot | None = None) -> int:
        await self.unload(bot_id)
        return await self.load_all(bot_id, bot)

    async def reload_single(self, module_name: str, bot_id: Any = None, bot: IBot | None = None) -> bool:
        await self.unload_single(module_name, bot_id)
        return await self.load_single(module_name, bot_id, bot)

    async def unload_single(self, module_name: str, bot_id: Any = None) -> None:
        bot_modules = self._modules.get(module_name)
        if not bot_modules:
            return
        instance = bot_modules.pop(bot_id, None)
        if instance:
            self._unregister_llm_hooks(instance, bot_id)
            try:
                await instance.on_unload()
            except Exception as e:
                self.log.warning(f"[Module] {module_name} on_unload 异常: {e}")
            self.services.task_manager.cancel_owner(f"module:{module_name}:{bot_id}")
            scheduler = self.services.scheduler if self.services else None
            if scheduler is not None:
                try:
                    await scheduler.unload_module(module_name, bot_id)
                except Exception as e:
                    self.log.warning(f"[Module] {module_name} 定时任务注销异常: {e}")
        if not bot_modules:
            del self._modules[module_name]

    def _unregister_llm_hooks(self, instance, bot_id) -> None:
        """卸载模块实例时注销其注册的 LLM 钩子。"""
        if not (self.services and self.services.agent_manager):
            return
        runtime = self.services.agent_manager.get_runtime(bot_id)
        if runtime is not None and hasattr(runtime, "llm_hooks"):
            runtime.llm_hooks.unregister_module(instance)
        if runtime is not None and hasattr(runtime, "llm_pipeline"):
            runtime.llm_pipeline.cancel_for_module(instance)

    @staticmethod
    def _purge_module_cache(module_name: str) -> None:
        prefix = f"module.modules.{module_name}"
        for key in [k for k in sys.modules if k == prefix or k.startswith(prefix + ".")]:
            del sys.modules[key]
