"""模块注册表：扫描 / 加载 / 热重载 / 安装 / 卸载业务模块。

替代原 modules/__init__.py 中的 ModuleManager：
- 插件必须位于 `<内置根>/<name>/main.py`（随仓库）或 `<外部根>/<name>/main.py`（zip 安装）；
- 每个模块按 (module_name, bot_id) 实例化，配置/权限来自 ConfigService；
- 热重载时彻底卸载旧实例（含其名下后台任务），再重新导入。

目录解析/来源判定/同名冲突**一律过 `ModuleCatalog`**（app/plugin/catalog.py）：
两个根是物理现实（内置随包发布、外部可写），统一的是"管理语义"而不是路径。
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import app.plugin.loader as loader
from app.core.logger import logger
from app.domain.bot import IBot
from app.plugin.authority import check_module_enabled
from app.plugin.base import BaseModule, ServiceAccess
from app.plugin.catalog import ModuleCatalog, ModuleEntry
from app.plugin.installer import InstallResult, ModuleInstallError, ZipPluginInstaller


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
        backup_dir: Path | str | None = None,
        staging_dir: Path | str | None = None,
    ) -> None:
        #: 业务插件代码目录（默认 `app/plugins`，随仓库走）
        self.plugins_dir = Path(plugins_dir)
        #: 外部 zip 插件安装目录（`data/module/plugins`，用户安装、不进仓库）
        self.zip_plugins_dir = Path(zip_plugins_dir) if zip_plugins_dir else None
        self.config_service = config_service
        self.services = services
        self.install_service = install_service
        self.log = log or logger
        self._modules: dict[str, dict[Any, BaseModule]] = {}
        #: 最近一次加载失败的原因（按模块名；UI 用它解释"为什么这个插件没生效"）
        self._load_errors: dict[str, str] = {}
        self.catalog = ModuleCatalog(
            builtin_dir=self.plugins_dir,
            installed_dir=self.zip_plugins_dir,
            install_service=install_service,
            log=self.log,
        )
        self._installer = ZipPluginInstaller(
            install_dir=self.zip_plugins_dir,
            builtin_dir=self.plugins_dir,
            install_service=install_service,
            log=self.log,
            staging_dir=staging_dir,
            backup_dir=backup_dir,
        )

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

    def account_modules(self, bot_id: Any) -> list[BaseModule]:
        """某个账号已装配的全部模块实例（不含全局 None 实例）。"""
        if bot_id is None:
            return []
        result: list[BaseModule] = []
        for bot_modules in self._modules.values():
            instance = bot_modules.get(bot_id)
            if instance is not None:
                result.append(instance)
        return result

    def load_error(self, module_name: str) -> str | None:
        """该模块最近一次加载失败的原因；加载成功/从未失败返回 None。"""
        return self._load_errors.get(str(module_name))

    def catalog_entries(self) -> list[ModuleEntry]:
        """统一编目（含未加载/加载失败/软卸载模块），并把运行期加载错误合并进条目。"""
        entries: list[ModuleEntry] = []
        for entry in self.catalog.scan():
            error = self._load_errors.get(entry.name)
            if not error:
                entries.append(entry)
            elif entry.status in ("ok", "manifest_missing"):
                entries.append(replace(entry, status="error", error=error))
            elif not entry.error:
                entries.append(replace(entry, error=error))
            else:
                entries.append(entry)
        return entries

    async def notify_online(self, bot_id: Any) -> int:
        """账号连上：对**已启用**的模块调用 ``on_online()`` 并补注册模块级定时任务。

        禁用模块不允许被武装（开关即契约，见 docs/multi-account-design.md §7）：
        否则 WebUI 里关掉的自定义任务会在账号重连后重新跑起来。返回实际武装的模块数；
        单个模块抛错只记日志——上线流程不能因为一个插件挂掉而中断。
        """
        instances = self.account_modules(bot_id)
        scheduler = getattr(self.services, "scheduler", None) if self.services else None
        armed = 0
        for instance in instances:
            if not check_module_enabled(instance):
                self.log.debug(
                    f"[Module] {instance.module_name}(bot {bot_id}) 已禁用：跳过上线武装"
                )
                continue
            armed += 1
            if scheduler is not None:
                try:
                    await scheduler.register_module(instance)
                except Exception as e:  # noqa: BLE001
                    self.log.warning(f"[Module] {instance.module_name} 上线后定时任务注册异常: {e}")
            try:
                await instance.on_online()
            except Exception as e:  # noqa: BLE001
                self.log.warning(f"[Module] {instance.module_name} on_online 异常: {e}")
        return armed

    async def notify_offline(self, bot_id: Any) -> int:
        """账号断开：对**已启用**的模块调用 ``on_offline()`` 并注销定时任务。

        与 :meth:`notify_online` 对称：禁用模块从未被武装，也就不需要（也不应该）再收一次
        ``on_offline``，避免向插件传递「你好像运行过」的错觉。
        """
        instances = self.account_modules(bot_id)
        scheduler = getattr(self.services, "scheduler", None) if self.services else None
        stopped = 0
        for instance in instances:
            if not check_module_enabled(instance):
                continue
            stopped += 1
            try:
                await instance.on_offline()
            except Exception as e:  # noqa: BLE001
                self.log.warning(f"[Module] {instance.module_name} on_offline 异常: {e}")
            if scheduler is not None:
                try:
                    await scheduler.unload_module(instance.module_name, bot_id)
                except Exception as e:  # noqa: BLE001
                    self.log.warning(f"[Module] {instance.module_name} 断线后定时任务注销异常: {e}")
        return stopped

    def rebind_bot(self, bot_id: Any, bot: IBot | None) -> int:
        """把某个账号所有已装配模块的 ``ctx.bot`` 重绑到当前连接。

        装配发生在建号时（那时还没有连接），登录时连接才出现；模块实例如果不重绑，
        ``ctx.bot`` 会一直是 None，凡是直接读 ``self.ctx.bot`` 的插件都会失效。
        """
        count = 0
        for instance in self.account_modules(bot_id):
            try:
                instance.ctx.bot = bot
                count += 1
            except Exception as e:  # noqa: BLE001
                self.log.debug(f"[Module] {instance.module_name} 重绑连接失败（已忽略）: {e}")
        return count

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
        """自定义配置页：插件根/pages/index.html（内置与外部同口径，修 P0-2）。"""
        entry = self.catalog.get(module_name)
        if entry is not None:
            page = entry.path / "pages" / "index.html"
            return page if page.is_file() else None
        module_path = self.catalog.resolve_path(module_name)
        if module_path is None:
            return None
        page = module_path / "pages" / "index.html"
        return page if page.is_file() else None

    def module_has_page(self, module_name: str) -> bool:
        return self.module_page_path(module_name) is not None

    # ---------- 编目 / 路径解析 ----------
    def _is_uninstalled(self, module_name: str) -> bool:
        if self.install_service is None:
            return False
        return bool(self.install_service.is_uninstalled(module_name))

    def _module_source(self, module_name: str) -> str:
        """返回模块来源：builtin（内置）或 installed（外部安装）。"""
        entry = self.catalog.get(module_name)
        if entry is not None:
            return entry.source
        if self._join_name(self.plugins_dir, module_name).is_dir():
            return "builtin"
        if self.zip_plugins_dir is not None and self._join_name(self.zip_plugins_dir, module_name).is_dir():
            return "installed"
        return "builtin"

    def _resolve_module_dir(self, module_name: str, source: str | None = None) -> Path:
        """解析模块目录；不存在时返回"按来源应当落到的路径"（调用方自行判定 is_dir）。

        source=None 时按"实际存在优先、内置优先"解析（旧调用语义）。
        """
        resolved = self.catalog.resolve_path(module_name, source=source)
        if resolved is not None:
            return resolved
        if self._normalize_source(source) == "installed" and self.zip_plugins_dir is not None:
            return self._join_name(self.zip_plugins_dir, module_name)
        return self._join_name(self.plugins_dir, module_name)

    def _resolve_module_path(self, module_name: str) -> Path:
        """兼容旧调用：返回模块实际目录（只存在一个来源时就是它）。"""
        source = self._module_source(module_name)
        resolved = self.catalog.resolve_path(module_name, source=source)
        return resolved if resolved is not None else self._resolve_module_dir(module_name, source)

    @staticmethod
    def _join_name(root: Path, module_name: str) -> Path:
        parts = [part for part in str(module_name).split(".") if part not in ("", ".")]
        return root.joinpath(*parts) if parts else root

    @staticmethod
    def _normalize_source(source: str | None) -> str | None:
        if source in ("builtin", "local"):
            return "builtin"
        if source in ("installed", "zip"):
            return "installed"
        return None

    @staticmethod
    def _read_manifest(module_dir: Path) -> dict:
        """读取 module.json（如有）。无 manifest 返回空 dict。

        保留该静态方法是为了兼容旧调用点；加载期真正消费元数据的是
        `app/plugin/loader.py::resolve_manifest`（带 v2 校验与兜底）。
        """
        manifest = module_dir / "module.json"
        if not manifest.is_file():
            return {}
        try:
            data = json.loads(manifest.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _ensure_installed_dir(self) -> None:
        """外部插件目录按需创建：用户从不装插件时它本来就不存在，不该刷 WARNING。"""
        if self.zip_plugins_dir is None:
            return
        try:
            self.zip_plugins_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.log.debug(f"[Module] 无法创建外部插件目录 {self.zip_plugins_dir}（已跳过）: {e}")

    def _known_account_ids(self) -> set[Any]:
        """已装配过的账号 id（安装/删除后给这些账号补装/清理实例）。"""
        return {
            bot_id
            for bot_modules in self._modules.values()
            for bot_id in bot_modules
            if bot_id is not None
        }

    # ---------- 加载 / 卸载 ----------
    async def load_single(
        self,
        module_name: str,
        bot_id: Any = None,
        bot: IBot | None = None,
        parent: BaseModule | None = None,
        purge: bool = False,
    ) -> bool:
        """加载一个插件（含递归子模块）。

        自己只做**编排**；每道工序在 `app/plugin/loader.py`：
        装配 → 绑钩子 → 连数据源 → 激活。全部成功后才写 `_modules`——
        失败不留幽灵条目/半装配实例（P0-7/P0-8）。

        ``purge=True`` 仅在显式热重载/覆盖安装时传：其它路径（按账号补实例、
        启动装配）复用已导入的模块对象，避免同一插件被 exec 多遍。
        """
        if module_name.startswith(("_", ".")) or self._is_uninstalled(module_name):
            return False
        source = self._module_source(module_name)
        module_path = self._resolve_module_dir(module_name, source)
        if not module_path.is_dir():
            self._record_load_error(module_name, f"模块目录不存在: {module_path}")
            return False

        entry = self.catalog.get(module_name)
        if entry is not None:
            if entry.source == "installed" and entry.status == "error":
                self._record_load_error(module_name, entry.error or "清单无效")
                self.log.error(f"[Module] {module_name} 未通过清单校验，拒绝加载: {entry.error}")
                return False
            if entry.status == "uninstalled":
                return False

        # 守卫：已有实例先卸载再装，避免重复 POST/热重载把钩子注册两遍（P1-3）
        if bot_id in self._modules.get(module_name, {}):
            await self.unload_single(module_name, bot_id)

        instance: BaseModule | None = None
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
                log=self.log,
                purge=purge,
            )
            if instance is None:
                self._record_load_error(module_name, "装配失败（入口文件/Module 类/清单不符合要求）")
                return False
            cls = type(instance)
            _module_hooks, llm_hooks = loader.bind_module_hooks(instance, cls)
            loader.bind_commands(instance, cls, self.log)
            loader.register_hooks(instance, cls, llm_hooks, bot_id, self.services, self.log)
            # 在线装配才注册模块级定时任务：离线装配不能武装任何计时器
            # （上线后由 `notify_online()` 补注册，见 BotService）
            await loader.register_data_sources(
                instance, module_name, self.services, self.log, online=bot is not None,
            )
            await loader.activate(instance, module_name, self.services, self.log)
        except Exception as e:  # noqa: BLE001 —— 单个插件失败不能中断整体加载
            self.log.exception(f"[Module] {module_name} (bot {bot_id}) 加载失败: {e}")
            self._record_load_error(module_name, f"{type(e).__name__}: {e}")
            if instance is not None:
                await self._cleanup_failed_instance(instance, module_name, bot_id)
            return False

        self._modules.setdefault(module_name, {})[bot_id] = instance
        self._load_errors.pop(module_name, None)

        # 递归加载子模块：父模块目录下、含 main.py 的子目录（忽略 service/ 等普通目录）
        try:
            child_dirs = sorted(module_path.iterdir())
        except OSError as e:
            self.log.warning(f"[Module] {module_name} 子模块目录读取失败（已忽略）: {e}")
            return True
        for child_entry in child_dirs:
            if not child_entry.is_dir() or child_entry.name.startswith(("_", ".")):
                continue
            if not (child_entry / "main.py").exists():
                continue
            child_name = f"{module_name}.{child_entry.name}"
            if bot_id in self._modules.get(child_name, {}):
                continue
            if await self.load_single(child_name, bot_id, bot, parent=instance, purge=purge):
                instance.children[child_entry.name] = self._modules[child_name][bot_id]
        return True

    def discover(self) -> int:
        """**只发现、不实例化**：扫一遍模块目录，返回可加载模块数。

        为什么需要它：模块实例是"按账号"的（配置/权限/开关都跟着账号走），
        启动时 `ensure_accounts()` 已经把每个账号的实例装好；再按 `bot_id=None`
        预加载一份"全局实例"只会多算一遍、多打一行日志，且让 WebUI/事件分发
        看到两套实例。所以启动只需要"发现"，装配交给账号。
        """
        self._ensure_installed_dir()
        names = [
            entry.name
            for entry in self.catalog.scan()
            # 加载失败/软卸载的模块不在"可加载"口径里；冲突仍由内置条目承担加载
            if entry.status not in ("error", "uninstalled")
        ]
        self.log.info(f"[Module] 模块预加载完成: {len(names)} 个")
        return len(names)

    async def load_all(self, bot_id: Any = None, bot: IBot | None = None, *, purge: bool = False) -> int:
        """加载编目里所有可加载模块到指定 bot_id。

        启动路径请用 `discover()`：账号实例由 `BotService.ensure_accounts()` 装配，
        这里只负责"补齐缺失实例"（登录时按需、热重载时），所以**只在真的补了东西时
        记 debug**，不再每账号打一行 INFO。**不预建空 dict**：失败不留幽灵条目。

        ``purge=True`` 供 `reload_all()` 使用：显式热重载要重新 exec 插件代码。
        """
        self._ensure_installed_dir()
        count = 0
        for entry in self.catalog.scan():
            if entry.status in ("error", "uninstalled"):
                continue
            if bot_id in self._modules.get(entry.name, {}):
                continue
            if await self.load_single(entry.name, bot_id, bot, purge=purge):
                count += 1
        if bot_id is None:
            self.log.info(f"[Module] 模块预加载完成: {count} 个新增")
        elif count:
            self.log.debug(f"[Module] Bot {bot_id} 补齐模块实例: {count} 个")
        return count

    async def unload(self, bot_id: Any = None) -> None:
        """卸载指定 bot_id 的所有模块实例（含 on_unload + 后台任务清理 + 定时任务注销）。"""
        for module_name, bot_modules in list(self._modules.items()):
            instance = bot_modules.pop(bot_id, None)
            if instance is None:
                continue
            await self._teardown_instance(instance, module_name, bot_id)
            if not bot_modules:
                del self._modules[module_name]

    async def reload_all(self, bot_id: Any = None, bot: IBot | None = None) -> int:
        await self.unload(bot_id)
        return await self.load_all(bot_id, bot, purge=True)

    async def reload_single(self, module_name: str, bot_id: Any = None, bot: IBot | None = None) -> bool:
        await self.unload_single(module_name, bot_id)
        return await self.load_single(module_name, bot_id, bot, purge=True)

    async def unload_single(self, module_name: str, bot_id: Any = None) -> None:
        bot_modules = self._modules.get(module_name)
        if not bot_modules:
            return
        instance = bot_modules.pop(bot_id, None)
        if instance is not None:
            await self._teardown_instance(instance, module_name, bot_id)
        if not bot_modules:
            del self._modules[module_name]

    async def _teardown_instance(self, instance: BaseModule, module_name: str, bot_id: Any) -> None:
        """彻底摘掉一个实例：钩子注册 / 数据源 / 节点 / 后台任务 / 定时任务。"""
        self._unregister_llm_hooks(instance, bot_id)
        self._unregister_plugin_hooks(instance)
        try:
            await instance.on_unload()
        except Exception as e:  # noqa: BLE001 —— 插件自身异常不能阻断卸载
            self.log.warning(f"[Module] {module_name} on_unload 异常: {e}")
        try:
            if self.services and getattr(self.services, "features", None):
                self.services.features.release_module(instance)
        except Exception as e:  # noqa: BLE001
            self.log.warning(f"[Module] {module_name} Feature 释放异常: {e}")
        if self.services is not None:
            task_manager = getattr(self.services, "task_manager", None)
            if task_manager is not None:
                try:
                    task_manager.cancel_owner(f"module:{module_name}:{bot_id}")
                except Exception as e:  # noqa: BLE001
                    self.log.warning(f"[Module] {module_name} 后台任务取消异常: {e}")
            scheduler = getattr(self.services, "scheduler", None)
            if scheduler is not None:
                try:
                    await scheduler.unload_module(module_name, bot_id)
                except Exception as e:  # noqa: BLE001
                    self.log.warning(f"[Module] {module_name} 定时任务注销异常: {e}")

    async def _cleanup_failed_instance(self, instance: BaseModule, module_name: str, bot_id: Any) -> None:
        """装配中途失败：把已经登记出去的东西摘干净（钩子/能力/任务），不留半成品。"""
        try:
            await self._teardown_instance(instance, module_name, bot_id)
        except Exception as e:  # noqa: BLE001 —— 清理失败不能盖住原始加载错误
            self.log.debug(f"[Module] {module_name} 失败实例清理异常（已忽略）: {e}")

    def _record_load_error(self, module_name: str, message: str) -> None:
        self._load_errors[str(module_name)] = message

    # ---------- 安装 / 更新 / 删除 ----------
    def _require_installer(self) -> None:
        if self.zip_plugins_dir is None:
            raise ModuleInstallError(
                "未配置外部插件目录（zip_plugins_dir）",
                hint="在 settings 里配置 module_dir（默认 data/module），外部插件装到 data/module/plugins",
            )
        if self.install_service is None:
            raise ModuleInstallError(
                "未配置安装状态服务（install_service）",
                hint="检查 app/bootstrap.py 是否装配了 ModuleInstallService",
            )

    def validate_zip(self, zip_path: str | Path) -> dict:
        """安装前预检（dry-run）：不落盘，返回将安装到的名字/版本/目标/冲突。"""
        if self.zip_plugins_dir is None:
            return {
                "ok": False, "plugin": {},
                "error": "未配置外部插件目录", "hint": "配置 data/module/plugins 后重试",
            }
        try:
            preview = self._installer.preflight(Path(zip_path))
        except ModuleInstallError as e:
            return {"ok": False, "plugin": {}, "error": str(e), "hint": e.hint}
        return {"ok": True, "plugin": preview, "error": "", "hint": ""}

    async def install_from_zip(self, zip_path: str | Path, *, force: bool = False) -> dict:
        """安装 zip 插件（事务化）：成功后加载并返回统一回执；失败抛 ModuleInstallError。"""
        self._require_installer()
        self._ensure_installed_dir()
        result = await self._installer.install(Path(zip_path), force=force)
        self.catalog.refresh()
        await self._load_after_install(result)
        self.catalog.refresh()
        return self._install_info(result)

    async def update_from_zip(self, zip_path: str | Path) -> dict:
        """覆盖安装（= install force=True）：旧版本自动备份，配置/数据默认保留。"""
        info = await self.install_from_zip(zip_path, force=True)
        info["updated"] = True
        return info

    async def delete_module(self, module_name: str, *, purge_data: bool = False) -> dict:
        """硬删除外部插件（内置拒绝）；配置默认保留，`purge_data=True` 才删运行时数据。"""
        self._require_installer()
        entry = self.catalog.get(module_name)
        if entry is None or not entry.path.is_dir():
            raise ValueError(f"模块 {module_name} 不存在")
        if entry.builtin:
            raise ModuleInstallError(
                "内置模块不可删除",
                hint="内置模块不可删除：只能禁用或软卸载（文件随仓库发布，删了下次启动还会出现）",
            )
        await self._unload_tree(module_name)
        data_dir = self.zip_plugins_dir.parent / "data" if purge_data else None
        result = await self._installer.delete(module_name, purge_data_dir=data_dir)
        self._load_errors.pop(module_name, None)
        self.catalog.refresh()
        return result

    async def _load_after_install(self, result: InstallResult) -> None:
        """安装成功后装配实例：已装配账号逐个补装；一个都没有时留一个全局实例。

        任一账号加载失败 → 回滚安装（删新版本、恢复备份、重载旧版本）再抛错，
        保证"不会装上一个跑不起来的版本，也不会弄丢原来能跑的版本"。
        """
        targets: list[Any] = sorted(self._known_account_ids(), key=str) or [None]
        failed = [bot_id for bot_id in targets if not await self.load_single(result.name, bot_id, purge=True)]
        if not failed:
            return

        detail = self._load_errors.get(result.name, "未知原因")
        try:
            self._installer.rollback(result)
        except ModuleInstallError as e:
            raise ModuleInstallError(
                f"插件 {result.name} 安装后加载失败，且回滚失败: {e}",
                hint=f"{detail}；{e.hint}",
            ) from e
        self.catalog.refresh()
        for bot_id in targets:
            try:
                await self.unload_single(result.name, bot_id)
                if result.backup is not None:
                    await self.load_single(result.name, bot_id, purge=True)
            except Exception as e:  # noqa: BLE001 —— 回滚后的恢复是尽力而为，不掩盖原始错误
                self.log.warning(f"[Module] 回滚后重载旧版本失败（已忽略）: {e}")
        raise ModuleInstallError(
            f"插件 {result.name} 安装后加载失败",
            hint=f"{detail}；已回滚到安装前状态（账号: {', '.join(str(item) for item in failed)}）",
        )

    def _install_info(self, result: InstallResult) -> dict:
        requirements = result.manifest.get("requirements")
        requirements = requirements if isinstance(requirements, str) and requirements else None
        info = {
            "module_name": result.name,
            "name": result.name,
            "display_name": result.display_name,
            "version": result.version,
            "source": "installed",
            "path": str(result.path),
            "target": str(result.path),
            "overwritten": result.overwritten,
            "backup": str(result.backup) if result.backup is not None else None,
            "files": result.files,
            "manifest": result.manifest,
            "requirements": requirements,
            "loaded": True,
        }
        if requirements and (result.path / requirements).is_file():
            info["requirements_hint"] = (
                f"插件声明了依赖清单 {requirements}，框架不会自动 pip 安装；请按需手工安装"
            )
        return info

    async def _unload_tree(self, module_name: str) -> None:
        """卸载模块及其全部子模块（cascade：先子后父）。"""
        names = [
            name for name in self._modules
            if name == module_name or name.startswith(module_name + ".")
        ]
        for name in sorted(names, key=lambda item: item.count("."), reverse=True):
            for bot_id in list(self._modules.get(name, {})):
                await self.unload_single(name, bot_id)

    # ---------- 软卸载 / 恢复安装 ----------
    async def uninstall_module(self, module_name: str) -> None:
        """软卸载：只写配置文件，不删除模块目录/配置/数据；子模块级联卸载。"""
        if self.install_service is None:
            raise ValueError("未配置安装状态服务 install_service")
        entry = self.catalog.get(module_name)
        if entry is None and not self._modules.get(module_name):
            raise ValueError(f"模块 {module_name} 不存在")

        source = entry.source if entry is not None else self._module_source(module_name)
        display_name = entry.display_name if entry is not None else module_name
        version = entry.version if entry is not None else ""
        instances = list(self._modules.get(module_name, {}).values())
        if instances:
            first = instances[0]
            display_name = getattr(first, "display_name", "") or display_name
            version = getattr(first, "version", "") or version
            source = getattr(first, "source", "") or source

        await self._unload_tree(module_name)
        self.install_service.uninstall(
            module_name,
            source=source,
            display_name=display_name or module_name,
            version=version,
        )
        self.log.info(f"[Module] 已软卸载 {module_name}（文件保留）")

    async def reinstall_module(self, module_name: str, bot_id: Any = None) -> bool:
        """清除软卸载记录并重新加载指定模块（先卸载旧实例，避免重复注册钩子）。"""
        if self.install_service is None:
            raise ValueError("未配置安装状态服务 install_service")
        entry = self.catalog.get(module_name)
        source = entry.source if entry is not None else self._module_source(module_name)
        module_path = self.catalog.resolve_path(module_name, source=source)
        if module_path is None:
            module_path = self._resolve_module_dir(module_name, source)
        if not module_path.is_dir():
            self.log.warning(f"[Module] 恢复安装失败：{module_name} 目录不存在: {module_path}")
            return False

        await self.unload_single(module_name, bot_id)
        self.install_service.reinstall(module_name)
        self.catalog.refresh()
        ok = await self.load_single(module_name, bot_id)
        if not ok:
            self.log.warning(f"[Module] 恢复安装 {module_name} 后加载失败，重新标记软卸载")
            self.install_service.uninstall(
                module_name,
                source=source,
                display_name=(entry.display_name if entry is not None else module_name),
                version=(entry.version if entry is not None else ""),
            )
        return ok

    # ---------- 钩子 / 数据源清理 ----------
    def _unregister_plugin_hooks(self, instance) -> None:
        """卸载模块实例时注销其全部插件钩子与全局注册（钩子 / 数据源 / 节点）。"""
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
        # 数据源（list/dynamic）：此前只有注册没有反注册，卸载后条目仍指向死实例
        data_sources = getattr(self.services, "data_sources", None) or getattr(
            self.services, "providers", None
        )
        unregister_ds = getattr(data_sources, "unregister_module", None)
        if callable(unregister_ds):
            try:
                unregister_ds(instance)
            except Exception as e:  # noqa: BLE001 —— 清理失败不该阻断卸载
                self.log.debug(f"[Module] 注销数据源失败（已忽略）: {e}")
        # 入站/出站节点：按归属批量移除
        nodes = getattr(self.services, "node_registry", None)
        remove_owner = getattr(nodes, "remove_owner", None)
        if callable(remove_owner):
            try:
                remove_owner(instance)
            except Exception as e:  # noqa: BLE001
                self.log.debug(f"[Module] 清理节点失败（已忽略）: {e}")
        # 上下文贡献者 / 工具源（runtime 级注册表）
        self._unregister_runtime_providers(instance)

    def _unregister_runtime_providers(self, instance) -> None:
        """移除模块登记的上下文贡献者与工具源（跨所有运行时）。"""
        manager = getattr(self.services, "agent_manager", None) if self.services else None
        if manager is None:
            return
        try:
            from app.agent.loop.providers import unregister_owner

            for runtime in manager.runtimes().values():
                unregister_owner(runtime, instance)
        except Exception as e:  # noqa: BLE001
            self.log.debug(f"[Module] 清理上下文/工具贡献者失败（已忽略）: {e}")

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
