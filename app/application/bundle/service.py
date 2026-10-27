"""套装服务：校验 / 导入登记 / 列表 / 导出 / 应用到账号 / 回滚。

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
from app.application.bundle.validator import inspect_zip, validate_bytes


class BundleService:
    def __init__(
        self,
        config_service: Any,
        *,
        data_root: Path | str,
        agent_manager: Any = None,
        installed_modules: dict[str, str] | None = None,
        agent_schema: dict | None = None,
        framework_api_version: int = 1,
        framework_version: str = "2.0.0",
    ) -> None:
        self.config_service = config_service
        self.agent_manager = agent_manager
        self.data_root = Path(data_root)
        self.installed_modules = installed_modules or {}
        self.agent_schema = agent_schema
        self.framework_api_version = framework_api_version
        self.framework_version = framework_version
        self.applier = BundleApplier(config_service)

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
            raise BundleError("BUNDLE_ID_INVALID", f"套装 id 不合法：{text or '(空)'}")
        return text

    # ==================== 校验 / 导入 ====================

    def validate(self, data: bytes) -> dict:
        return validate_bytes(
            data,
            framework_api_version=self.framework_api_version,
            framework_version=self.framework_version,
            installed_modules=self.installed_modules,
            agent_schema=self.agent_schema,
        )

    async def import_bundle(self, data: bytes) -> dict:
        """校验通过后把包落盘登记（不应用）。返回报告 + 落盘路径。"""
        report = self.validate(data)
        if not report["ok"]:
            errors = [issue for issue in report["issues"] if issue["level"] == "error"]
            first = errors[0]["title"] if errors else "预检未通过"
            raise BundleError("BUNDLE_INVALID", f"预检未通过：{first}", "修正后再导入")

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
    ) -> bytes:
        """把某账号的当前配置导出成套装 zip（密钥被剥离成 secrets 声明）。"""
        bundle_id = self._safe_bundle_id(bundle_id)
        agent_config = self.config_service.get_module_config("agent", account_id) or {}

        persona = None
        persona_id = str(agent_config.get("persona_preset_id") or "")
        if persona_id:
            preset = self.config_service.get_role_preset(persona_id)
            if preset:
                persona = {"name": preset.get("name", ""), "text": preset.get("system_prompt", "")}

        provider = None
        preset_id = str(agent_config.get("provider_preset_id") or "")
        pool = agent_config.get("provider_model_pool") or []
        model_id = str(pool[0]) if pool else str(agent_config.get("provider_model_id") or "")
        if preset_id:
            preset = self.config_service.get_provider_preset(preset_id)
            model_row = self.config_service.get_provider_model(model_id) if model_id else None
            if preset and model_row:
                provider = {
                    "provider": preset.get("provider", "openai"),
                    "api_base": (preset.get("config") or {}).get("api_base", ""),
                    "api_key": (preset.get("config") or {}).get("api_key", ""),
                    "model": model_row.get("model", ""),
                    "preset_name": preset.get("name", ""),
                }

        module_configs: dict[str, dict] = {}
        if include_modules:
            for module_name in self.installed_modules:
                config = self.config_service.get_module_config(module_name, account_id) or {}
                authority = self.config_service.get_module_authority(module_name, account_id) or {}
                if not config and not authority:
                    continue
                module_configs[module_name] = {
                    "enabled": bool(authority.get("enabled", True)),
                    "config": config,
                }

        return export_bundle(
            bundle_id=bundle_id,
            name=name,
            version=version,
            description=description,
            agent_config=agent_config,
            persona=persona,
            provider=provider,
            module_configs=module_configs,
        )


    # ==================== 应用到账号 / 回滚（P3） ====================

    def _load_parts(self, bundle_id: str) -> tuple[bytes, dict, dict]:
        """读已登记套装 → (原始字节, 归一清单, 内容物)。"""
        path = self.bundle_file(bundle_id)
        if path is None:
            raise BundleError("BUNDLE_NOT_FOUND", f"套装不存在：{bundle_id}", "先导入套装")
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
    ) -> dict:
        """把套装应用到某账号（事务写 + 快照，失败自动回滚）。"""
        _, manifest, files = self._load_parts(bundle_id)
        return await self.applier.apply(
            bundle_id, str(account_id), manifest, files, secrets=secrets or {}
        )

    async def rollback(self, activation_id: str) -> dict:
        return await self.applier.rollback(str(activation_id))

    async def recover_interrupted(self) -> list[str]:
        return await self.applier.recover_interrupted()

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
