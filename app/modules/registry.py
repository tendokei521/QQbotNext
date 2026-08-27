"""模块注册表：扫描 / 加载 / 热重载 / 卸载业务模块。

替代原 modules/__init__.py 中的 ModuleManager：
- 模块必须位于 <modules_dir>/<name>/module.py，导出 `Module(BaseModule)`；
- 每个模块按 (module_name, bot_id) 实例化，配置/权限来自 ConfigService；
- 热重载时彻底卸载旧实例（含其名下后台任务），再重新导入。
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import tempfile
import zipfile
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
        plugins_dir: Path | str | None = None,
        install_service: Any = None,
    ) -> None:
        self.modules_dir = Path(modules_dir)
        self.plugins_dir = Path(plugins_dir) if plugins_dir else None
        self.config_service = config_service
        self.services = services
        self.install_service = install_service
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
        """自定义配置页：模块目录/pages/index.html。无则返回 None。"""
        page = self._resolve_module_path(module_name) / "pages" / "index.html"
        return page if page.is_file() else None

    def module_has_page(self, module_name: str) -> bool:
        return self.module_page_path(module_name) is not None

    # ---------- 软卸载状态 ----------
    def _is_uninstalled(self, module_name: str) -> bool:
        if self.install_service is None:
            return False
        return bool(self.install_service.is_uninstalled(module_name))

    def _module_source(self, module_name: str) -> str:
        """返回模块来源：local 或 zip。"""
        local = self._resolve_module_dir(module_name, source="local")
        if local and local.is_dir():
            return "local"
        plugin = self._resolve_module_dir(module_name, source="zip")
        if plugin and plugin.is_dir():
            return "zip"
        return "local"

    @staticmethod
    def _read_manifest(module_dir: Path) -> dict:
        """读取 module.json（如有）。无 manifest 返回空 dict。"""
        manifest = module_dir / "module.json"
        if not manifest.is_file():
            return {}
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    # ---------- 加载 / 卸载 ----------
    def _resolve_module_dir(self, module_name: str, source: str | None = None) -> Path:
        """解析模块目录路径。

        优先 module/modules/<name>，其次 module/plugins/<name>。
        source 可强制指定 local 或 zip。
        """
        parts = module_name.split(".")
        if source == "zip" and self.plugins_dir is not None:
            path = self.plugins_dir
        else:
            path = self.modules_dir
        for p in parts:
            path = path / p
        return path

    def _resolve_module_path(self, module_name: str):
        """兼容旧调用：返回实际存在的模块目录。"""
        return self._resolve_module_dir(module_name)

    def _iter_module_roots(self):
        """返回 [(dir, source)]：本地模块目录 + 外部插件目录。"""
        roots = [(self.modules_dir, "local")]
        if self.plugins_dir is not None:
            roots.append((self.plugins_dir, "zip"))
        return roots

    async def load_single(
        self,
        module_name: str,
        bot_id: Any = None,
        bot: IBot | None = None,
        parent: BaseModule | None = None,
    ) -> bool:
        if self._is_uninstalled(module_name):
            return False
        source = self._module_source(module_name)
        module_path = self._resolve_module_dir(module_name, source)
        if not module_path.is_dir() or module_name.startswith(("_", ".")):
            return False

        try:
            self._purge_module_cache(module_name)
            import_path = (
                f"module.modules.{module_name}.module"
                if source == "local"
                else f"module.plugins.{module_name}.module"
            )
            mod = importlib.import_module(import_path)
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
            instance.source = source
            instance.version = str(self._read_manifest(module_path).get("version", "") or "")
            instance.plugin_dir = str(module_path)
            instance.can_uninstall = True  # 本地/外部插件都允许软卸载（文件保留）
            if not instance.version:
                instance.version = "0.0.0"

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
            # 注册消息发送成功钩子（@send_hook）
            send_hooks = cls.collect_send_hooks()
            if send_hooks and self.services and self.services.send_hooks:
                for hook in send_hooks:
                    handler = getattr(instance, hook["method"], None)
                    if handler is None:
                        continue
                    self.services.send_hooks.register(
                        bot_id=bot_id,
                        module=instance,
                        handler=handler,
                        message_type=hook.get("message_type", "*"),
                        order=hook.get("order", 100),
                    )

            # 注册消息发送前钩子（@before_send_hook）
            before_send_hooks = cls.collect_before_send_hooks()
            if before_send_hooks and self.services and self.services.before_send_hooks:
                for hook in before_send_hooks:
                    handler = getattr(instance, hook["method"], None)
                    if handler is None:
                        continue
                    self.services.before_send_hooks.register(
                        bot_id=bot_id,
                        module=instance,
                        handler=handler,
                        message_type=hook.get("message_type", "*"),
                        order=hook.get("order", 100),
                    )

            # 注册任意 API 调用后钩子（@api_hook）
            api_hooks = cls.collect_api_hooks()
            if api_hooks and self.services and self.services.api_hooks:
                for hook in api_hooks:
                    handler = getattr(instance, hook["method"], None)
                    if handler is None:
                        continue
                    self.services.api_hooks.register(
                        bot_id=bot_id,
                        module=instance,
                        handler=handler,
                        action=hook.get("action", "*"),
                        order=hook.get("order", 100),
                    )

            # 注册 Bot 生命周期钩子（@bot_lifecycle_hook）
            lifecycle_hooks = cls.collect_lifecycle_hooks()
            if lifecycle_hooks and self.services and self.services.lifecycle_hooks:
                for hook in lifecycle_hooks:
                    handler = getattr(instance, hook["method"], None)
                    if handler is None:
                        continue
                    self.services.lifecycle_hooks.register(
                        bot_id=bot_id,
                        module=instance,
                        handler=handler,
                        state=hook.get("state", "*"),
                        order=hook.get("order", 100),
                    )

            # 注册事件处理完成钩子（@event_completed_hook）
            event_completed_hooks = cls.collect_event_completed_hooks()
            if event_completed_hooks and self.services and self.services.event_completed_hooks:
                for hook in event_completed_hooks:
                    handler = getattr(instance, hook["method"], None)
                    if handler is None:
                        continue
                    self.services.event_completed_hooks.register(
                        bot_id=bot_id,
                        module=instance,
                        handler=handler,
                        order=hook.get("order", 100),
                    )

            # 注册模块工具（@tool / TOOLS）与技能（@skill / SKILLS）到该 Bot 的 AgentRuntime
            if self.services and self.services.agent_manager:
                runtime = self.services.agent_manager.get_runtime(bot_id)
                if runtime is not None:
                    runtime.llm_tools.register_module(instance)
                    runtime.skills.register_module(instance)
                    # 注册 LLM 工具调用后钩子（@tool_call_hook）
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
        """加载本地模块与外部插件到指定 bot_id。"""
        count = 0
        seen: set[str] = set()
        for root, source in self._iter_module_roots():
            if not root.exists():
                self.log.warning(f"[Module] {source} 模块目录不存在: {root}")
                continue
            for entry in sorted(root.iterdir()):
                if not entry.is_dir() or entry.name.startswith(("_", ".")):
                    continue
                if entry.name in seen:
                    continue
                seen.add(entry.name)
                if self._is_uninstalled(entry.name):
                    continue
                if entry.name not in self._modules:
                    self._modules[entry.name] = {}
                if bot_id not in self._modules[entry.name]:
                    if await self.load_single(entry.name, bot_id, bot):
                        count += 1
        if bot_id is None:
            self.log.info(f"[Module] 模块预加载完成: {count} 个新增")
        else:
            self.log.info(f"[Module] Bot {bot_id} 模块加载完成: {count} 个新增")
        return count

    async def unload(self, bot_id: Any = None) -> None:
        """卸载指定 bot_id 的所有模块实例（含 on_unload + 后台任务清理 + 定时任务注销）。"""
        for module_name, bot_modules in list(self._modules.items()):
            instance = bot_modules.pop(bot_id, None)
            if instance is None:
                continue
            self._unregister_llm_hooks(instance, bot_id)
            self._unregister_plugin_hooks(instance)
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
            self._unregister_plugin_hooks(instance)
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

    # ---------- zip 安装 / 软卸载 / 恢复安装 ----------
    async def install_from_zip(self, zip_path: str | Path) -> dict:
        """安装 zip 插件到 module/plugins/<name>，然后加载全局实例。

        不删除已有文件：若目标已存在且处于软卸载状态，则只清除卸载记录并重新加载。
        """
        if self.plugins_dir is None:
            raise ValueError("未配置外部插件目录 plugins_dir")
        if self.install_service is None:
            raise ValueError("未配置安装状态服务 install_service")
        zip_path = Path(zip_path)
        if not zip_path.is_file():
            raise ValueError(f"插件压缩包不存在: {zip_path}")

        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.plugins_dir, prefix=".zip-") as tmp:
            tmp_dir = Path(tmp)
            self._extract_zip_plugin(zip_path, tmp_dir)
            manifest = self._find_manifest(tmp_dir)
            name = str((manifest.get("name") or "").strip())
            if not name:
                raise ValueError("module.json 缺少 name 字段")
            if name.startswith(("_", ".")) or "/" in name or "\\" in name:
                raise ValueError(f"非法插件名: {name}")

            root = tmp_dir
            if tmp_dir.name != name:
                # 若压缩包含单一顶层目录，取其内容
                if len([p for p in tmp_dir.iterdir() if p.is_dir()]) == 1 and (
                    tmp_dir / name
                ).is_dir():
                    root = tmp_dir / name
            final_dir = self.plugins_dir / name
            if final_dir.exists():
                if not self.install_service.is_uninstalled(name):
                    raise ValueError(f"插件 {name} 已安装，请先卸载或使用覆盖安装")
                # 软卸载状态：复用已有文件，不清除/覆盖
                self.install_service.reinstall(name)
                return await self._after_install_from_zip(name, manifest)

            # 移动解压内容到最终目录
            if root == tmp_dir:
                shutil.copytree(str(tmp_dir), str(final_dir))
            else:
                shutil.move(str(root), str(final_dir))

            self.install_service.reinstall(name)
            return await self._after_install_from_zip(name, manifest)

    async def _after_install_from_zip(self, name: str, manifest: dict) -> dict:
        """安装/恢复后加载全局实例，返回模块信息。"""
        ok = await self.load_single(name, None)
        if not ok:
            raise ValueError(f"插件 {name} 安装后加载失败")
        return {
            "module_name": name,
            "display_name": manifest.get("display_name") or name,
            "version": manifest.get("version", ""),
            "source": "zip",
        }

    async def uninstall_module(self, module_name: str) -> None:
        """软卸载：只写配置文件，不删除模块目录/配置/数据。"""
        if self.install_service is None:
            raise ValueError("未配置安装状态服务 install_service")
        instances = list(self._modules.get(module_name, {}).values())
        display_name = ""
        version = ""
        source = "local"
        if instances:
            first = instances[0]
            display_name = getattr(first, "name", "") or ""
            version = getattr(first, "version", "") or ""
            source = getattr(first, "source", self._module_source(module_name)) or "local"
        else:
            source = self._module_source(module_name)

        for bot_id in list(self._modules.get(module_name, {}).keys()):
            await self.unload_single(module_name, bot_id)

        self.install_service.uninstall(
            module_name,
            source=source,
            display_name=display_name or module_name,
            version=version,
        )
        self.log.info(f"[Module] 已软卸载 {module_name}（文件保留）")

    async def reinstall_module(self, module_name: str, bot_id: Any = None) -> bool:
        """清除软卸载记录并重新加载指定模块。"""
        if self.install_service is None:
            raise ValueError("未配置安装状态服务 install_service")
        source = self._module_source(module_name)
        module_path = self._resolve_module_dir(module_name, source)
        if not module_path.is_dir():
            self.log.warning(f"[Module] 恢复安装失败：{module_name} 目录不存在: {module_path}")
            return False
        self.install_service.reinstall(module_name)
        ok = await self.load_single(module_name, bot_id)
        if not ok:
            self.log.warning(f"[Module] 恢复安装 {module_name} 后加载失败，重新标记软卸载")
            self.install_service.uninstall(
                module_name,
                source=source,
                display_name=module_name,
            )
        return ok

    @staticmethod
    def _extract_zip_plugin(zip_path: Path, target_dir: Path) -> None:
        """校验并安全解压插件 zip。"""
        try:
            with zipfile.ZipFile(zip_path, "r") as z:
                names = z.namelist()
                if not names:
                    raise ValueError("插件压缩包为空")
                for entry in names:
                    normalized = entry.replace("\\", "/")
                    if normalized.startswith("/") or ".." in normalized.split("/"):
                        raise ValueError(f"插件压缩包包含非法路径: {entry}")
                if not ModuleRegistry._find_manifest_entry(names):
                    raise ValueError("压缩包不是合法插件：未找到 module.json")
                z.extractall(target_dir)
        except zipfile.BadZipFile as e:
            raise ValueError("插件压缩包格式错误") from e

    @staticmethod
    def _find_manifest_entry(names: list[str]) -> str | None:
        for name in names:
            if name.replace("\\", "/").endswith("module.json"):
                return name
        return None

    @staticmethod
    def _find_manifest(root: Path) -> dict:
        """在解压目录中查找 module.json 并解析。"""
        candidates = [
            root / "module.json",
            *[p / "module.json" for p in root.iterdir() if p.is_dir()],
        ]
        for cfg in candidates:
            if cfg.is_file():
                try:
                    data = json.loads(cfg.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as e:
                    raise ValueError("module.json 格式错误") from e
                if not isinstance(data, dict):
                    raise ValueError("module.json 必须为 JSON 对象")
                return data
        raise ValueError("未找到 module.json")

    def _unregister_plugin_hooks(self, instance) -> None:
        """卸载模块实例时注销其全部插件钩子。"""
        if self.services is None:
            return
        for attr in ("send_hooks", "before_send_hooks", "api_hooks", "lifecycle_hooks", "event_completed_hooks"):
            reg = getattr(self.services, attr, None)
            if reg is not None:
                reg.unregister_module(instance)
        if self.services.agent_manager is not None:
            for runtime in self.services.agent_manager.runtimes().values():
                reg = getattr(runtime, "llm_tool_call_hooks", None)
                if reg is not None:
                    reg.unregister_module(instance)

    def _unregister_llm_hooks(self, instance, bot_id) -> None:
        """卸载模块实例时注销其注册的 LLM 钩子 / 工具 / 技能。"""
        if not (self.services and self.services.agent_manager):
            return
        runtime = self.services.agent_manager.get_runtime(bot_id)
        if runtime is None:
            return
        if hasattr(runtime, "llm_hooks"):
            runtime.llm_hooks.unregister_module(instance)
        if hasattr(runtime, "llm_tools"):
            runtime.llm_tools.unregister_module(instance)
        if hasattr(runtime, "skills"):
            runtime.skills.unregister_module(instance)
        if hasattr(runtime, "llm_pipeline"):
            runtime.llm_pipeline.cancel_for_module(instance)

    @staticmethod
    def _purge_module_cache(module_name: str) -> None:
        prefixes = (
            f"module.modules.{module_name}",
            f"module.plugins.{module_name}",
        )
        for prefix in prefixes:
            for key in [k for k in sys.modules if k == prefix or k.startswith(prefix + ".")]:
                del sys.modules[key]
