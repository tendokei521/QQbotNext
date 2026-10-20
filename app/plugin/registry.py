"""模块注册表：扫描 / 加载 / 热重载 / 卸载业务模块。

替代原 modules/__init__.py 中的 ModuleManager：
- 插件必须位于 <plugins_dir>/<name>/module.py，导出 `Module(BaseModule)`；
- 每个模块按 (module_name, bot_id) 实例化，配置/权限来自 ConfigService；
- 热重载时彻底卸载旧实例（含其名下后台任务），再重新导入。
"""

from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import app.plugin.loader as loader
from app.core.logger import logger
from app.domain.bot import IBot
from app.plugin.base import BaseModule, ServiceAccess


class ModuleRegistry:
    def __init__(
        self,
        *,
        plugins_dir: Path | str,
        config_service: Any,
        services: ServiceAccess,
        log=None,
        zip_plugins_dir: Path | str | None = None,
        install_service: Any = None,
    ) -> None:
        #: 业务插件代码目录（默认 `app/plugins`，随仓库走）
        self.plugins_dir = Path(plugins_dir)
        #: 外部 zip 插件安装目录（`module/plugins`，用户安装、不进仓库）
        self.zip_plugins_dir = Path(zip_plugins_dir) if zip_plugins_dir else None
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

        优先本地插件目录（默认 `app/plugins/<name>`），其次外部 zip 安装目录
        （`module/plugins/<name>`）。source 可强制指定 local 或 zip。
        """
        parts = module_name.split(".")
        if source == "zip" and self.zip_plugins_dir is not None:
            path = self.zip_plugins_dir
        else:
            path = self.plugins_dir
        for p in parts:
            path = path / p
        return path

    def _resolve_module_path(self, module_name: str):
        """兼容旧调用：返回实际存在的模块目录。"""
        return self._resolve_module_dir(module_name)

    def _iter_module_roots(self):
        """返回 [(dir, source)]：本地模块目录 + 外部插件目录。"""
        roots = [(self.plugins_dir, "local")]
        if self.zip_plugins_dir is not None:
            roots.append((self.zip_plugins_dir, "zip"))
        return roots

    async def load_single(
        self,
        module_name: str,
        bot_id: Any = None,
        bot: IBot | None = None,
        parent: BaseModule | None = None,
    ) -> bool:
        """加载一个插件（含递归子模块）。

        自己只做**编排**；每道工序在 `app/plugin/loader.py`：
        装配 → 绑钩子 → 连数据源 → 激活。
        """
        if self._is_uninstalled(module_name):
            return False
        source = self._module_source(module_name)
        module_path = self._resolve_module_dir(module_name, source)
        if not module_path.is_dir() or module_name.startswith(("_", ".")):
            return False

        try:
            instance = loader.instantiate_module(
                module_name=module_name,
                module_path=module_path,
                source=source,
                bot_id=bot_id,
                bot=bot,
                parent=parent,
                config_service=self.config_service,
                services=self.services,
                read_manifest=self._read_manifest,
                log=self.log,
            )
            if instance is None:
                return False
            cls = type(instance)

            _module_hooks, llm_hooks = loader.bind_module_hooks(instance, cls)
            self._modules.setdefault(module_name, {})[bot_id] = instance
            loader.register_hooks(instance, cls, llm_hooks, bot_id, self.services, self.log)
            await loader.register_data_sources(instance, module_name, self.services, self.log)
            await loader.activate(instance, module_name, self.services, self.log)

            # 递归加载子模块：父模块目录下、含 module.py 的子目录（忽略 service/ 等普通目录）
            for child_entry in sorted(module_path.iterdir()):
                if not child_entry.is_dir() or child_entry.name.startswith(("_", ".")):
                    continue
                if not (child_entry / "module.py").exists():
                    continue
                child_name = f"{module_name}.{child_entry.name}"
                if bot_id not in self._modules.setdefault(child_name, {}):
                    if await self.load_single(child_name, bot_id, bot, parent=instance):
                        instance.children[child_entry.name] = self._modules[child_name][bot_id]

            return True
        except Exception as e:  # noqa: BLE001 —— 单个插件失败不能中断整体加载
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
            try:
                self.services.features.release_module(instance) if self.services and self.services.features else 0
            except Exception as e:
                self.log.warning(f"[Module] {module_name} Feature 释放异常: {e}")
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
            try:
                self.services.features.release_module(instance) if self.services and self.services.features else 0
            except Exception as e:
                self.log.warning(f"[Module] {module_name} Feature 释放异常: {e}")
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
        """安装 zip 插件到 zip_plugins_dir（默认 module/plugins/<name>），然后加载全局实例。

        不删除已有文件：若目标已存在且处于软卸载状态，则只清除卸载记录并重新加载。
        """
        if self.zip_plugins_dir is None:
            raise ValueError("未配置外部插件目录 zip_plugins_dir")
        if self.install_service is None:
            raise ValueError("未配置安装状态服务 install_service")
        zip_path = Path(zip_path)
        if not zip_path.is_file():
            raise ValueError(f"插件压缩包不存在: {zip_path}")

        self.zip_plugins_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.zip_plugins_dir, prefix=".zip-") as tmp:
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
            final_dir = self.zip_plugins_dir / name
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


