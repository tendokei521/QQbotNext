"""预设服务：校验 / 导入登记 / 列表 / 导出 / 应用到账号 / 回滚。

P2 负责"工件进得来、看得懂、导得出"；P3 起增加 `apply` / `rollback`
（写账号配置走 `ConfigService.apply_ops` 单事务，激活记录兼作 journal）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app.application.bundle.applier import BundleApplier
from app.application.bundle.errors import BundleError
from app.application.bundle.exporter import export_bundle
from app.application.bundle.manifest import normalize_manifest
from app.application.bundle.module_packages import ModulePackageError, dir_size, pack_module_dir
from app.application.bundle.validator import inspect_zip, validate_bytes


class BundleService:
    def __init__(
        self,
        config_service: Any,
        *,
        data_root: Path | str,
        agent_manager: Any = None,
        installed_modules: dict[str, str] | None = None,
        module_catalog: dict[str, dict] | None = None,
        module_installer: Any = None,
        agent_schema: dict | None = None,
        framework_api_version: int = 1,
        framework_version: str = "2.0.0",
    ) -> None:
        self.config_service = config_service
        self.agent_manager = agent_manager
        self.data_root = Path(data_root)
        self.installed_modules = installed_modules or {}
        #: 模块编目（name → {version, builtin, path}）：导出时决定"打包代码还是只声明版本"
        self.module_catalog = module_catalog or {}
        #: 模块安装器（可选）：随包模块在应用阶段安装/回滚时卸载
        self.module_installer = module_installer
        self.agent_schema = agent_schema
        self.framework_api_version = framework_api_version
        self.framework_version = framework_version
        self.applier = BundleApplier(
            config_service,
            installed_modules=self.installed_modules,
            module_installer=module_installer,
        )

    # ==================== 路径 ====================

    @property
    def bundles_root(self) -> Path:
        return self.data_root / "bundles"

    def _bundle_dir(self, bundle_id: str) -> Path:
        return self.bundles_root / bundle_id

    @staticmethod
    def _safe_bundle_id(bundle_id: str) -> str:
        text = str(bundle_id or "").strip()
        if not text or "/" in text or "\\" in text or ".." in text:
            raise BundleError("BUNDLE_ID_INVALID", f"预设 id 不合法：{text or '(空)'}")
        return text

    # ==================== 校验 / 导入 ====================

    def validate(self, data: bytes) -> dict:
        return validate_bytes(
            data,
            framework_api_version=self.framework_api_version,
            framework_version=self.framework_version,
            installed_modules=self.installed_modules,
            agent_schema=self.agent_schema,
            builtin_modules={
                name for name, item in self.module_catalog.items() if item.get("builtin")
            },
        )

    async def import_bundle(self, data: bytes, *, accept_code: bool = True) -> dict:
        """校验通过后把包落盘登记（不应用）。返回报告 + 落盘路径。

        ``accept_code``：包内带可执行模块代码时必须由用户显式确认信任（API 默认 False，
        WebUI 勾选后传 True）；内部调用/测试默认 True 保持旧行为。
        """
        report = self.validate(data)
        if not report["ok"]:
            errors = [issue for issue in report["issues"] if issue["level"] == "error"]
            first = errors[0]["title"] if errors else "预检未通过"
            raise BundleError("BUNDLE_INVALID", f"预检未通过：{first}", "修正后再导入")
        if report.get("requires_code_consent") and not accept_code:
            raise BundleError(
                "BUNDLE_CODE_CONSENT_REQUIRED",
                "预设包含可执行的模块代码，需要确认信任后才能导入",
                "勾选「我信任此预设包含的模块代码」后重试",
            )

        bundle = report["bundle"]
        bundle_id = self._safe_bundle_id(bundle["id"])
        target_dir = self._bundle_dir(bundle_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{bundle_id}-{bundle['version']}.zip"
        target = target_dir / filename
        target.write_bytes(data)
        meta = {
            "id": bundle_id,
            "name": bundle["name"],
            "version": bundle["version"],
            "description": bundle.get("description", ""),
            "imported_at": int(time.time()),
            "size": len(data),
            "file": filename,
            "secrets": report.get("secrets", []),
            # 卡片/详情展示用：包含哪些模块、有多少是随包代码
            "modules": [item["name"] for item in report.get("modules", [])],
            "modules_bundled": int(report["summary"].get("modules_bundled", 0)),
            "modules_to_install": int(report["summary"].get("modules_to_install", 0)),
            # 应用对话框据此默认勾选「自动安装依赖」并列出缺依赖的模块
            "modules_missing_deps": int(report["summary"].get("modules_missing_deps", 0)),
            "deps_missing_modules": [
                item["name"] for item in report.get("modules", [])
                if item.get("missing_requirements")
            ],
            "has_code": bool(report["summary"].get("modules_bundled", 0)),
        }
        (target_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {**report, "path": str(target), "file": filename}

    # ==================== 列表 / 下载 ====================

    def list_installed(self) -> list[dict]:
        if not self.bundles_root.is_dir():
            return []
        out: list[dict] = []
        for entry in sorted(self.bundles_root.iterdir()):
            if not entry.is_dir():
                continue
            meta_path = entry / "meta.json"
            if not meta_path.is_file():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            meta["installed"] = True
            out.append(meta)
        out.sort(key=lambda item: int(item.get("imported_at") or 0), reverse=True)
        return out

    def bundle_file(self, bundle_id: str) -> Path | None:
        directory = self._bundle_dir(self._safe_bundle_id(bundle_id))
        meta_path = directory / "meta.json"
        if not meta_path.is_file():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        target = directory / str(meta.get("file") or "")
        return target if target.is_file() else None

    # ==================== 导出 ====================

    def export_account(
        self,
        account_id: str,
        *,
        bundle_id: str,
        name: str,
        version: str = "1.0.0",
        description: str = "",
        include_modules: bool = True,
        module_filter: list[str] | None = None,
    ) -> bytes:
        """把某账号的当前配置导出成预设 zip（密钥被剥离成 secrets 声明）。

        ``module_filter``：只导出这些模块（``None`` = 全部）。未列出的模块既不带配置
        也不带代码，导入方保持原样。
        """
        bundle_id = self._safe_bundle_id(bundle_id)
        selected = None if module_filter is None else {
            str(item) for item in module_filter if str(item or "").strip()
        }
        agent_config = self.config_service.get_module_config("agent", account_id) or {}

        persona = None
        persona_id = str(agent_config.get("persona_preset_id") or "")
        if persona_id:
            preset = self.config_service.get_role_preset(persona_id)
            if preset:
                persona = {
                    "name": preset.get("name", ""),
                    "text": preset.get("system_prompt", ""),
                    "description": preset.get("description", ""),
                    "begin_dialogs": preset.get("begin_dialogs") or [],
                    "tools": preset.get("tools"),
                    "skills": preset.get("skills"),
                }

        provider = None
        preset_id = str(agent_config.get("provider_preset_id") or "")
        pool = [str(item) for item in (agent_config.get("provider_model_pool") or []) if str(item)]
        if not pool and agent_config.get("provider_model_id"):
            pool = [str(agent_config["provider_model_id"])]
        if preset_id:
            preset = self.config_service.get_provider_preset(preset_id)
            models = []
            for model_id in pool:
                model_row = self.config_service.get_provider_model(model_id)
                if not model_row:
                    continue
                models.append({
                    "model": model_row.get("model", ""),
                    "provider_type": model_row.get("provider_type") or "chat",
                    "enabled": bool(model_row.get("enabled", True)),
                    "config": model_row.get("config") or {},
                })
            if preset and models:
                provider_config = {
                    key: value
                    for key, value in (preset.get("config") or {}).items()
                    if key != "api_key"
                }
                provider = {
                    "provider": preset.get("provider", "openai"),
                    "api_base": provider_config.get("api_base", ""),
                    "api_key": (preset.get("config") or {}).get("api_key", ""),
                    "model": models[0]["model"],
                    "preset_name": preset.get("name", ""),
                    "config": provider_config,
                    "models": models,
                }

        module_configs: dict[str, dict] = {}
        module_packages: dict[str, bytes] = {}
        module_meta: dict[str, dict] = {}
        if include_modules:
            for module_name in self.installed_modules:
                if selected is not None and module_name not in selected:
                    continue
                config = self.config_service.get_module_config(module_name, account_id) or {}
                authority = self.config_service.get_module_authority(module_name, account_id) or {}
                if not config and not authority:
                    continue
                module_configs[module_name] = {
                    "enabled": bool(authority.get("enabled", True)),
                    "config": config,
                }
                catalog_entry = self.module_catalog.get(module_name) or {}
                builtin = bool(catalog_entry.get("builtin", True))
                module_version = str(
                    catalog_entry.get("version") or self.installed_modules.get(module_name) or ""
                )
                module_meta[module_name] = {"version": module_version, "builtin": builtin}
                if builtin:
                    continue
                directory = catalog_entry.get("path")
                if not directory:
                    continue
                try:
                    module_packages[module_name] = pack_module_dir(directory)
                except ModulePackageError as e:
                    # 单个模块打不进包不能让整个导出失败：清单里仍声明它（目标机需自行安装）
                    self.config_service.log.warning(
                        f"[Bundle] 模块 {module_name} 未能随包导出：{e}"
                    )

        return export_bundle(
            bundle_id=bundle_id,
            name=name,
            version=version,
            description=description,
            agent_config=agent_config,
            persona=persona,
            provider=provider,
            module_configs=module_configs,
            module_packages=module_packages,
            module_meta=module_meta,
        )

    def export_options(self, account_id: str) -> list[dict]:
        """导出对话框的模块清单：名称 / 版本 / 是否内置 / 体积 / 当前启停。"""
        options: list[dict] = []
        for module_name, version in self.installed_modules.items():
            entry = self.module_catalog.get(module_name) or {}
            builtin = bool(entry.get("builtin", True))
            directory = entry.get("path")
            authority = self.config_service.get_module_authority(module_name, account_id) or {}
            options.append({
                "name": module_name,
                "display_name": str(entry.get("display_name") or module_name),
                "version": str(entry.get("version") or version or ""),
                "builtin": builtin,
                "enabled": bool(authority.get("enabled", True)),
                "has_config": bool(
                    self.config_service.get_module_config(module_name, account_id) or {}
                ),
                "size": 0 if builtin or not directory else dir_size(directory),
            })
        options.sort(key=lambda item: (item["builtin"], item["name"]))
        return options

    # ==================== 应用到账号 / 回滚（P3） ====================

    def _load_parts(self, bundle_id: str) -> tuple[bytes, dict, dict]:
        """读已登记预设 → (原始字节, 归一清单, 内容物)。"""
        path = self.bundle_file(bundle_id)
        if path is None:
            raise BundleError("BUNDLE_NOT_FOUND", f"预设不存在：{bundle_id}", "先导入预设")
        data = path.read_bytes()
        report = self.validate(data)
        if not report["ok"]:
            first = next((i for i in report["issues"] if i["level"] == "error"), None)
            raise BundleError("BUNDLE_INVALID", f"预检未通过：{first['title'] if first else '未知原因'}")
        manifest_raw, files, _checksums = inspect_zip(data)
        return data, normalize_manifest(manifest_raw), files

    async def apply(
        self,
        bundle_id: str,
        account_id: str,
        *,
        secrets: dict | None = None,
        reuse_provider_preset_id: str | None = None,
    ) -> dict:
        """把预设应用到某账号（事务写 + 快照，失败自动回滚）。"""
        _, manifest, files = self._load_parts(bundle_id)
        return await self.applier.apply(
            bundle_id, str(account_id), manifest, files, secrets=secrets or {},
            reuse_provider_preset_id=reuse_provider_preset_id,
        )

    async def rollback(self, activation_id: str, *, keep_modules: bool = False) -> dict:
        return await self.applier.rollback(str(activation_id), keep_modules=keep_modules)

    async def recover_interrupted(self) -> list[str]:
        return await self.applier.recover_interrupted()

    async def uninstall(
        self,
        bundle_id: str,
        account_id: str | None = None,
        *,
        keep_modules: bool = False,
    ) -> dict:
        """卸载预设：先回滚其激活，再删记账与工件文件。

        边界（P4-4）：
        - 只回滚本预设的激活记录，**不动用户后建的对象**（`bundle_objects` 只记本预设创建的）；
        - 若该账号还有其它预设处于 active，不在这里处理（各自独立回滚）；
        - 工件文件只有在**没有任何激活记录再引用它**时才删除，避免误删仍在用的包；
        - ``keep_modules=True``：只回滚配置，保留随包安装的模块（来源台账也保留）。
        """
        db = self.config_service.db
        bundle_id = self._safe_bundle_id(bundle_id)
        params: list = [bundle_id]
        sql = "SELECT id FROM bundle_activations WHERE bundle_id = ? AND status = ?"
        params.append("active")
        if account_id:
            sql += " AND account_id = ?"
            params.append(str(account_id))
        rows = await db.fetchall(sql, tuple(params))

        rolled_back: list[str] = []
        kept_modules: list[str] = []
        for row in rows or []:
            result = await self.applier.rollback(row["id"], keep_modules=keep_modules)
            rolled_back.append(row["id"])
            kept_modules.extend(result.get("modules_kept") or [])

        # 记账清理：仅清与本次卸载范围相关的行
        if account_id:
            await db.execute(
                "DELETE FROM bundle_objects WHERE bundle_id = ? AND account_id = ?",
                (bundle_id, str(account_id)),
            )
        else:
            await db.execute("DELETE FROM bundle_objects WHERE bundle_id = ?", (bundle_id,))

        # 仍有其它激活引用它 → 保留工件
        remaining = await db.fetchone(
            "SELECT COUNT(*) AS total FROM bundle_activations WHERE bundle_id = ? AND status = ?",
            (bundle_id, "active"),
        )
        deleted_artifact = False
        if not remaining or not int(remaining["total"] or 0):
            directory = self._bundle_dir(bundle_id)
            if directory.is_dir() and self.bundles_root in directory.parents:
                for item in sorted(directory.iterdir()):
                    if item.is_file():
                        item.unlink()
                directory.rmdir()
                deleted_artifact = True

        return {
            "bundle_id": bundle_id,
            "rolled_back": rolled_back,
            "deleted_artifact": deleted_artifact,
            "modules_kept": kept_modules,
        }

    async def list_activations(self, account_id: str | None = None) -> list[dict]:
        """激活记录（按时间倒序）。"""
        db = self.config_service.db
        if account_id:
            rows = await db.fetchall(
                "SELECT * FROM bundle_activations WHERE account_id = ? ORDER BY started_at DESC",
                (str(account_id),),
            )
        else:
            rows = await db.fetchall("SELECT * FROM bundle_activations ORDER BY started_at DESC")
        return [
            {
                "id": row["id"],
                "bundle_id": row["bundle_id"],
                "account_id": row["account_id"],
                "status": row["status"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "error": row["error"],
            }
            for row in (rows or [])
        ]
