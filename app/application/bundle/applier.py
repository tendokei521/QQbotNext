"""预设应用到账号：计划 → 快照 → 单事务写入 → 校验 → 记录；失败可回滚。

设计依据：`docs/oneclick-bundle-and-onboarding-design.md` §6.3（7 步事务）。

关键取舍：
- **配置写入走 `ConfigService.apply_ops`（单事务）**：要么全成，要么内存/DB 都回到应用前；
- **随包模块先安装、再写配置**：模块代码走既有 zip 安装通道（`module_installer`），
  安装失败的模块会被记住，回滚时只卸载"本次新装"的（升级的保留，避免删掉用户原有模块）；
- **预设的"创建了什么"单独记账**（`bundle_objects`），卸载/回滚只删这些对象；
- **激活记录本身就是 journal**：`applying` 行在启动时被 `recover_interrupted()` 自动回滚。
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any

from app.application.bundle.errors import BundleError
from app.application.bundle.manifest import satisfies_version
from app.application.bundle.origins import clear_origin, record_origin
from app.infrastructure.config.config_service import ConfigOp

AGENT_CAPABILITY = "agent"

#: 可勾选的内容分类（导出 / 应用共用）。顺序即界面展示顺序。
ALL_SECTIONS: tuple[str, ...] = ("persona", "provider", "agent", "modules")
_SECTION_LABELS = {
    "persona": "人格",
    "provider": "模型配置",
    "agent": "AI 设置",
    "modules": "插件",
}

#: 密钥声明里的键路径 → (kind, target, field)
_PROVIDER_SECRET_RE = re.compile(r"^providers\[(\d+)\]\.(.+)$")
_PROVIDER_MODEL_SECRET_RE = re.compile(r"^provider_models\[(\d+)\]\.(.+)$")
_MODULE_SECRET_RE = re.compile(r"^module:([A-Za-z0-9_\-]+)\.(.+)$")
_AGENT_SECRET_RE = re.compile(r"^agent\.(.+)$")


def _read_json(files: dict[str, bytes], name: str) -> dict:
    payload = files.get(name)
    if payload is None:
        return {}
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise BundleError("BUNDLE_VALUE_INVALID", f"{name} 不是合法 JSON：{e}") from e
    if not isinstance(data, dict):
        raise BundleError("BUNDLE_VALUE_INVALID", f"{name} 必须是 JSON 对象")
    return data


def _secret_section(key: str) -> str:
    """密钥声明属于哪个内容分类；``unknown`` 表示未知路径。"""
    kind, _target, _field = _split_secret_key(key)
    if kind in ("provider", "provider_model"):
        return "provider"
    if kind == "agent":
        return "agent"
    if kind == "module":
        return "modules"
    return "unknown"


def normalize_sections(selected: Any) -> set[str]:
    """校验内容分类集合；空集合 / 未知分类直接报错。"""
    if selected is None:
        return set(ALL_SECTIONS)
    names = {str(item).strip() for item in selected if str(item or "").strip()}
    unknown = names - set(ALL_SECTIONS)
    if unknown:
        raise BundleError(
            "BUNDLE_SECTION_UNKNOWN",
            f"不认识的内容分类：{'、'.join(sorted(unknown))}",
            "检查导出/应用参数",
        )
    if not names:
        raise BundleError(
            "BUNDLE_NOTHING_SELECTED",
            "至少选择一项要处理的内容",
            "勾选人格、模型配置、AI 设置或插件后重试",
        )
    return names


def _split_secret_key(key: str) -> tuple[str, str, str]:
    """``secrets[].key`` → ``(kind, target, field)``；无法识别返回 ``("unknown", "", "")``。"""
    text = str(key or "").strip()
    match = _PROVIDER_SECRET_RE.match(text)
    if match:
        return "provider", match.group(1), match.group(2)
    match = _PROVIDER_MODEL_SECRET_RE.match(text)
    if match:
        return "provider_model", match.group(1), match.group(2)
    match = _MODULE_SECRET_RE.match(text)
    if match:
        return "module", match.group(1), match.group(2)
    match = _AGENT_SECRET_RE.match(text)
    if match:
        return "agent", "", match.group(1)
    return "unknown", "", ""


def select_components(
    manifest: dict,
    selected: set[str],
    module_filter: set[str] | None = None,
) -> dict:
    """按勾选内容裁出"这次真正要应用"的组件（清单其余字段保持不变）。"""
    components = dict(manifest.get("components") or {})
    selected_modules = [
        item for item in (components.get("modules") or [])
        if "modules" in selected
        and (module_filter is None or str(item.get("name") or "") in module_filter)
    ]
    result = dict(manifest)
    result["components"] = {
        "personas": list(components.get("personas") or []) if "persona" in selected else [],
        "providers": list(components.get("providers") or []) if "provider" in selected else [],
        "agent": list(components.get("agent") or []) if "agent" in selected else [],
        "modules": selected_modules,
    }
    return result


def select_secrets(
    manifest: dict,
    selected: set[str],
    module_names: set[str] | None = None,
) -> list[dict]:
    """只保留与勾选内容相关的密钥声明（没勾的不会要求填）。"""
    kept: list[dict] = []
    for secret in manifest.get("secrets") or []:
        section = _secret_section(str(secret.get("key") or ""))
        if section == "unknown":
            # 未知路径没法和分类对应：只有"全选"时才要求，避免部分应用被未知密钥卡住
            if selected == set(ALL_SECTIONS):
                kept.append(secret)
            continue
        if section not in selected:
            continue
        if section == "modules":
            match = _MODULE_SECRET_RE.match(str(secret.get("key") or ""))
            module_name = match.group(1) if match else ""
            if module_names is not None and module_name not in module_names:
                continue
        kept.append(secret)
    return kept


class BundleApplier:
    def __init__(
        self,
        config_service: Any,
        *,
        installed_modules: dict[str, str] | None = None,
        module_installer: Any = None,
    ) -> None:
        self.config_service = config_service
        self.installed_modules = dict(installed_modules or {})
        #: 模块安装器（可选）：需实现 ``async install(name, zip_bytes) -> dict`` /
        #: ``async uninstall(name) -> None``；不传 = 不支持随包安装模块。
        self.module_installer = module_installer

    # ==================== 计划 ====================

    def build_plan(
        self,
        manifest: dict,
        files: dict[str, bytes],
        *,
        account_id: str,
        secrets: dict | None = None,
        previous: dict | None = None,
        reuse_provider_preset_id: str | None = None,
        sections: Any = None,
        modules: Any = None,
    ) -> dict:
        """把「清单 + 内容物 + 目标账号现状」算成幂等计划（含内部 ops）。

        ``sections``：只应用这些内容分类（``persona`` / ``provider`` / ``agent`` /
        ``modules``）；``None`` = 全部。``modules``：只应用这些插件的配置；``None`` = 全部。

        ``previous`` = 上一次同等价应用于该账号时记录的 ``applied_json``
        （``{"agent": {...}, "modules": {name: {...}}}``）。升级同 id 新版本时据此
        **保留用户改动**：用户改过的键不再被新包覆盖；已填密钥缺省复用旧值。

        ``reuse_provider_preset_id``：复用本机已有 Provider 预设的密钥（"一键"场景，
        用户不想重填 Key 时选一个已连通的连接）。
        """
        selected = normalize_sections(sections)
        module_filter = (
            None if modules is None
            else {str(item).strip() for item in modules if str(item or "").strip()}
        )
        # 只保留这次勾选的组件：后续步骤/密钥/自检都基于裁剪后的清单
        full_manifest = manifest
        manifest = select_components(manifest, selected, module_filter)
        applied_module_names = {
            str(item.get("name") or "") for item in manifest["components"]["modules"]
        }
        provided = {
            str(key): str(value)
            for key, value in (secrets or {}).items()
            if str(value or "").strip()
        }
        previous = previous or {}
        prev_agent = dict(previous.get("agent") or {})
        prev_modules = dict(previous.get("modules") or {})
        account_id = str(account_id)
        agent_row = self.config_service.get_binding(AGENT_CAPABILITY, "account", account_id) or {}
        agent_config = dict(agent_row.get("config") or {})
        agent_permission = dict(agent_row.get("permission") or {})
        agent_enabled = agent_row.get("enabled")
        preserved: list[str] = []

        agent_patch: dict[str, Any] = {}
        create_ops: list[ConfigOp] = []
        creates: list[dict] = []
        creates_meta: list[dict] = []

        # 1) 人格：全文落成新的角色预设（不覆盖任何既有预设），角色卡字段一起带过来
        for persona in manifest["components"]["personas"]:
            text = files.get(persona["source"], b"").decode("utf-8").strip()
            if not text:
                continue
            role_id = f"role_{uuid.uuid4().hex[:10]}"
            preset = {
                "id": role_id,
                "name": persona["name"],
                "description": persona.get("description") or "由预设导入",
                "system_prompt": text,
                "begin_dialogs": list(persona.get("begin_dialogs") or []),
                "tools": persona.get("tools"),
                "skills": persona.get("skills"),
                "created_at": int(time.time()),
                "updated_at": int(time.time()),
            }
            create_ops.append(ConfigOp("role_preset", (role_id,), preset))
            creates_meta.append({"kind": "role_preset", "id": role_id})
            creates.append({"kind": "role_preset", "name": persona["name"]})
            agent_patch["persona_preset_id"] = role_id

        # 2) Provider 连接 + 模型池（密钥来自导入时手填 / 复用本机已有预设；升级时缺省复用旧值）
        provider_config: dict[str, Any] = {}
        provider_models: list[dict] = []
        reused_from = ""
        provider = (manifest["components"]["providers"] or [None])[0]
        if provider:
            provider_config = dict(provider.get("config") or {})
            provider_config["api_base"] = provider["api_base"]
            api_key = provided.get("providers[0].api_key", "")
            if not api_key and reuse_provider_preset_id:
                reused = self.config_service.get_provider_preset(reuse_provider_preset_id)
                if not reused:
                    raise BundleError(
                        "BUNDLE_REUSE_PRESET_NOT_FOUND",
                        f"要复用的 Provider 预设不存在：{reuse_provider_preset_id}",
                        "刷新 Provider 预设列表后重选，或手填密钥",
                    )
                api_key = str((reused.get("config") or {}).get("api_key") or "")
                if not api_key:
                    raise BundleError(
                        "BUNDLE_REUSE_PRESET_EMPTY",
                        f"Provider 预设「{reused.get('name') or reuse_provider_preset_id}」没有可复用的密钥",
                        "换一个预设，或手填密钥",
                    )
                reused_from = str(reuse_provider_preset_id)
            if not api_key:
                for created in previous.get("created") or []:
                    if created.get("kind") == "provider_preset":
                        old_preset = self.config_service.get_provider_preset(created["id"]) or {}
                        api_key = str((old_preset.get("config") or {}).get("api_key") or "")
                        break
            provider_config["api_key"] = api_key
            # 其余 provider 级密钥声明（如 providers[0].timeout）必须在建预设**之前**写进
            # provider_config，否则预设里存的是旧拷贝、写库后再改内存就丢了。
            for secret_key, secret_value in provided.items():
                kind, target, field = _split_secret_key(secret_key)
                if kind == "provider" and target == "0" and field != "api_key":
                    provider_config[field] = secret_value

            preset_id = f"pp_{uuid.uuid4().hex[:10]}"
            preset = {
                "id": preset_id,
                "name": provider["preset_name"] or provider["provider"],
                "provider": provider["provider"],
                "config": dict(provider_config),
                "enabled": True,
                "created_at": int(time.time()),
                "updated_at": int(time.time()),
            }
            create_ops.append(ConfigOp("provider_preset", (preset_id,), preset))
            creates_meta.append({"kind": "provider_preset", "id": preset_id})
            creates.append({"kind": "provider_preset", "name": preset["name"]})

            model_rows = provider.get("models") or [{"model": provider["model"]}]
            pool: list[str] = [
                str(item) for item in (agent_config.get("provider_model_pool") or []) if str(item)
            ]
            for model in model_rows:
                model_id = f"pm_{uuid.uuid4().hex[:10]}"
                model_config = dict(model.get("config") or {})
                row = {
                    "id": model_id,
                    "preset_id": preset_id,
                    "model": str(model.get("model") or provider["model"]),
                    "provider_type": str(model.get("provider_type") or "chat"),
                    "enabled": bool(model.get("enabled", True)),
                    "config": model_config,
                    "created_at": int(time.time()),
                    "updated_at": int(time.time()),
                }
                create_ops.append(ConfigOp("provider_model", (model_id,), row))
                creates_meta.append({"kind": "provider_model", "id": model_id})
                creates.append({"kind": "provider_model", "name": row["model"]})
                provider_models.append(row)
                if model_id not in pool:
                    pool.append(model_id)
            if provider_models:
                agent_patch["provider_preset_id"] = preset_id
                agent_patch["provider_model_id"] = provider_models[0]["id"]
                agent_patch["provider_model_pool"] = pool

        # 3) Agent 配置子集
        for source in manifest["components"]["agent"]:
            agent_patch.update(_read_json(files, source["source"]))

        # 用户改过的键（当前值 != 上次应用值）保留用户值，不被新包覆盖；
        # 指向本预设新建对象的引用键总是覆盖（用户不直接编辑它们）。
        for key in list(agent_patch.keys()):
            if key in ("persona_preset_id", "provider_preset_id", "provider_model_id"):
                continue
            prev_value = prev_agent.get(key)
            if prev_value is not None and agent_config.get(key) != prev_value:
                agent_patch.pop(key)
                preserved.append(f"agent.{key}")

        # 4) 模块：配置合并 + 启停（先算合并值，密钥稍后按声明写回）
        module_patches: list[dict] = []
        for module in manifest["components"]["modules"]:
            capability = module["name"]
            row = self.config_service.get_binding(capability, "account", account_id) or {}
            merged = {**(row.get("config") or {})}
            if module.get("source"):
                incoming = _read_json(files, module["source"])
                prev_module = prev_modules.get(capability) or {}
                for key, value in incoming.items():
                    if key in prev_module and (row.get("config") or {}).get(key) != prev_module[key]:
                        preserved.append(f"module:{capability}.{key}")
                        continue
                    merged[key] = value
            module_patches.append({
                "capability": capability,
                "row": row,
                "merged": merged,
                # 包里有 enabled 才改启停；没有就保持账号现状（None = 继承账号默认值）。
                # 老包里缺这个键曾被当成 True，导致「应用配置包 → 所有插件被打开」。
                "enabled": (
                    bool(module["enabled"])
                    if module.get("enabled") is not None
                    else row.get("enabled")
                ),
            })

        # 5) 密钥按声明键路径写回（v1 只认 providers[0].api_key，模块/Agent 密钥被静默丢弃）
        for key, value in provided.items():
            kind, target, field = _split_secret_key(key)
            if not field:
                continue
            if kind == "agent":
                agent_patch[field] = value
            elif kind == "provider_model" and provider_models:
                index = int(target)
                if 0 <= index < len(provider_models):
                    provider_models[index].setdefault("config", {})[field] = value
            elif kind == "module":
                for patch in module_patches:
                    if patch["capability"] == target:
                        patch["merged"][field] = value
                        break

        # 6) Agent 绑定 + 模块绑定写操作
        new_agent_config = {**agent_config, **agent_patch}
        ops: list[ConfigOp] = list(create_ops)
        bindings_snapshot = [
            {"capability": AGENT_CAPABILITY, "scope_type": "account", "scope_id": account_id,
             "row": dict(agent_row) if agent_row else None}
        ]
        ops.append(ConfigOp(
            "binding", (AGENT_CAPABILITY, "account", account_id),
            {"enabled": agent_enabled, "config": new_agent_config,
             "permission": agent_permission, "priority": int(agent_row.get("priority", 100))},
        ))
        updates = [{"target": "agent", "keys": sorted(agent_patch)}]

        applied_modules: dict[str, dict] = {}
        for patch in module_patches:
            capability = patch["capability"]
            row = patch["row"]
            merged = patch["merged"]
            ops.append(ConfigOp(
                "binding", (capability, "account", account_id),
                {"enabled": patch["enabled"], "config": merged,
                 "permission": dict(row.get("permission") or {}),
                 "priority": int(row.get("priority", 100))},
            ))
            bindings_snapshot.append({
                "capability": capability, "scope_type": "account", "scope_id": account_id,
                "row": dict(row) if row else None,
            })
            applied_modules[capability] = merged
            updates.append({"target": f"module:{capability}", "keys": sorted(merged)})

        # 只要求"这次勾选内容"相关的密钥：没勾模型配置就不该被 API Key 卡住
        selected_secrets = select_secrets(manifest, selected, applied_module_names)
        plan = {
            "account_id": account_id,
            "bundle": {"id": manifest["id"], "name": manifest["name"], "version": manifest["version"]},
            "creates": creates,
            "updates": updates,
            "sections": sorted(selected),
            "secrets_required": [
                secret for secret in selected_secrets if secret.get("required", True)
            ],
            "modules_to_install": self._pending_modules(
                full_manifest, module_filter if "modules" in selected else set()
            ),
            "reused_provider_preset_id": reused_from,
            "snapshot": {
                "bindings": bindings_snapshot,
                "created": creates_meta,
                "active_bundle": None,
                "modules_installed": [],
                "modules_upgraded": [],
            },
            "applied": {"agent": new_agent_config, "modules": applied_modules},
            "preserved": preserved,
            "ops": ops,
        }
        self._require_secrets(selected_secrets, {
            "provider_config": provider_config,
            "provider_models": provider_models,
            "agent": new_agent_config,
            "modules": applied_modules,
        }, provided)
        return plan

    def _pending_modules(self, manifest: dict, module_filter: set[str] | None = None) -> list[str]:
        """随包且目标机缺/旧（应用时需要安装或升级）的模块名。

        ``module_filter``：只算这些插件；``None`` = 全部，空集合 = 一个都不装。
        """
        pending: list[str] = []
        for module in manifest["requires"]["modules"]:
            if module_filter is not None and module["name"] not in module_filter:
                continue
            if not module.get("source"):
                continue
            current = str(self.installed_modules.get(module["name"]) or "")
            if current and satisfies_version(current, module.get("version") or ""):
                continue
            pending.append(module["name"])
        return pending

    @staticmethod
    def _require_secrets(secrets: list[dict], resolved: dict, provided: dict | None = None) -> None:
        """必填密钥在"写回后的最终配置"里必须有值（否则应用出来的是半套）。"""
        provided = provided or {}
        missing: list[str] = []
        for secret in secrets:
            if not secret.get("required", True):
                continue
            key = str(secret.get("key") or "")
            kind, target, field = _split_secret_key(key)
            if kind == "unknown":
                # 我们不认识的键路径：用户已填就放行（写不进去会在别处暴露），没填则点名
                if key and key not in provided:
                    missing.append(secret.get("label") or key)
                continue
            value: Any = None
            if kind == "provider":
                value = (resolved.get("provider_config") or {}).get(field) if target == "0" else None
            elif kind == "provider_model":
                index = int(target)
                models = resolved.get("provider_models") or []
                if 0 <= index < len(models):
                    value = (models[index].get("config") or {}).get(field)
            elif kind == "agent":
                value = (resolved.get("agent") or {}).get(field)
            elif kind == "module":
                value = (resolved.get("modules") or {}).get(target, {}).get(field)
            if not str(value or "").strip():
                missing.append(secret.get("label") or key or "(未命名密钥)")
        if missing:
            raise BundleError(
                "BUNDLE_SECRET_MISSING",
                f"缺少必填密钥：{'、'.join(missing)}",
                "在应用对话框里填写这些密钥后重试（密钥不随包分发）",
            )

    # ==================== 模块安装 ====================

    async def _install_modules(
        self,
        manifest: dict,
        files: dict[str, bytes],
        account_id: str = "",
        module_filter: set[str] | None = None,
    ) -> tuple[list[str], list[str], list[dict]]:
        """安装随包模块 → ``(新装, 升级, 依赖安装回执)``；不需要安装时返回空列表。

        ``module_filter``：只安装这些插件；``None`` = 全部，空集合 = 一个都不装。

        成功安装/升级后写「模块来源台账」（模块页显示"来自预设 xxx"）。
        勾选了自动装依赖时，模块安装器会在回执里带 ``deps``（pip 结果），这里原样
        收集给前端展示；对"已满足版本、被跳过"的随包模块，再让安装器补一次依赖
        （换机时可能只拷了配置和模块代码，依赖还缺）。
        """
        targets = [
            module for module in manifest["requires"]["modules"]
            if module.get("source")
            and (module_filter is None or module["name"] in module_filter)
        ]
        if not targets:
            return [], [], []
        if self.module_installer is None:
            names = "、".join(module["name"] for module in targets)
            raise BundleError(
                "BUNDLE_MODULE_INSTALL_UNSUPPORTED",
                f"当前实例不支持自动安装随包模块：{names}",
                "先手动安装这些模块，或使用带模块安装能力的 WebUI",
            )
        db = self.config_service.db
        installed: list[str] = []
        upgraded: list[str] = []
        deps_log: list[dict] = []
        for module in targets:
            name = module["name"]
            payload = files.get(module["source"])
            if payload is None:
                raise BundleError("BUNDLE_REF_MISSING", f"模块代码包不存在：{module['source']}")
            current = str(self.installed_modules.get(name) or "")
            if current and satisfies_version(current, module.get("version") or ""):
                continue
            try:
                info = await self.module_installer.install(name, payload)
            except Exception as e:  # noqa: BLE001 —— 安装异常统一转成预设错误码
                raise BundleError(
                    "BUNDLE_MODULE_INSTALL_FAILED",
                    f"安装模块 {name} 失败：{e}",
                    "检查模块包是否合法、文件是否被占用后重试",
                ) from e
            version = str((info or {}).get("version") or "")
            self.installed_modules[name] = version or self.installed_modules.get(name, "")
            (upgraded if current else installed).append(name)
            deps = (info or {}).get("deps")
            if isinstance(deps, dict):
                deps_log.append({"module": name, "deps": deps})
            await record_origin(
                db, name,
                bundle_id=str(manifest["id"]),
                bundle_name=str(manifest.get("name") or ""),
                bundle_version=str(manifest.get("version") or ""),
                account_id=str(account_id or ""),
                action="upgraded" if current else "installed",
            )
        ensure_deps = getattr(self.module_installer, "ensure_dependencies", None)
        if callable(ensure_deps):
            handled = set(installed) | set(upgraded)
            for module in targets:
                name = module["name"]
                if name in handled:
                    continue  # 本次刚装/升级的模块已在安装回执里带过依赖结果
                current = str(self.installed_modules.get(name) or "")
                if not (current and satisfies_version(current, module.get("version") or "")):
                    continue
                try:
                    deps = await ensure_deps(name)
                except Exception as e:  # noqa: BLE001 —— 补装失败不阻断配置应用，如实记录
                    self.config_service.log.warning(
                        f"[Bundle] 模块 {name} 补装依赖失败: {e}"
                    )
                    continue
                if isinstance(deps, dict):
                    deps_log.append({"module": name, "deps": deps})
        return installed, upgraded, deps_log

    async def _uninstall_modules(self, names: list[str] | None) -> list[str]:
        """卸载"本次新装"的模块（升级的不动：它可能是用户原本就有的模块）。"""
        removed: list[str] = []
        if self.module_installer is None:
            return removed
        db = self.config_service.db
        for name in names or []:
            try:
                await self.module_installer.uninstall(name)
                removed.append(name)
                await clear_origin(db, name)
            except Exception as e:  # noqa: BLE001 —— 单人卸载失败不能盖住回滚结果
                self.config_service.log.warning(f"[Bundle] 回滚时卸载模块 {name} 失败: {e}")
        return removed

    # ==================== 应用 ====================

    async def apply(
        self,
        bundle_id: str,
        account_id: str,
        manifest: dict,
        files: dict[str, bytes],
        *,
        secrets: dict | None = None,
        reuse_provider_preset_id: str | None = None,
        sections: Any = None,
        modules: Any = None,
    ) -> dict:
        db = self.config_service.db
        selected = normalize_sections(sections)
        module_filter = (
            None if modules is None
            else {str(item).strip() for item in modules if str(item or "").strip()}
        )
        if "modules" not in selected:
            module_filter = set()
        # 升级 diff：取该 (bundle, account) 最近一次 active 的 applied 快照与记账对象
        previous = None
        prev_row = await db.fetchone(
            "SELECT applied_json, snapshot_json FROM bundle_activations "
            "WHERE bundle_id = ? AND account_id = ? AND status = ? ORDER BY started_at DESC LIMIT 1",
            (bundle_id, str(account_id), "active"),
        )
        if prev_row is not None:
            previous = db.loads(prev_row["applied_json"], {}) or {}
            prev_objects = await db.fetchall(
                "SELECT kind, object_id FROM bundle_objects WHERE bundle_id = ? AND account_id = ?",
                (bundle_id, str(account_id)),
            )
            previous["created"] = [
                {"kind": row["kind"], "id": row["object_id"]} for row in (prev_objects or [])
            ]
        plan = self.build_plan(
            manifest, files, account_id=account_id, secrets=secrets, previous=previous,
            reuse_provider_preset_id=reuse_provider_preset_id,
            sections=selected, modules=modules,
        )
        activation_id = f"act_{uuid.uuid4().hex[:12]}"
        now = int(time.time())
        plan_public = {
            key: value for key, value in plan.items()
            if key not in ("ops",) and not str(key).startswith("_")
        }

        await db.execute(
            "INSERT INTO bundle_activations "
            "(id, bundle_id, account_id, status, plan_json, snapshot_json, applied_json, error, started_at, finished_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (activation_id, bundle_id, account_id, "applying", db.dumps(plan_public),
             db.dumps(plan["snapshot"]), "{}", "", now, 0),
        )

        applied = False
        installed: list[str] = []
        upgraded: list[str] = []
        deps_log: list[dict] = []
        try:
            # 模块代码先落地：装不上就别写配置（避免"配置指向不存在的模块"）
            installed, upgraded, deps_log = await self._install_modules(
                manifest, files, str(account_id), module_filter
            )
            if installed or upgraded:
                snapshot = dict(plan["snapshot"])
                snapshot["modules_installed"] = list(installed)
                snapshot["modules_upgraded"] = list(upgraded)
                plan["snapshot"] = snapshot
                await db.execute(
                    "UPDATE bundle_activations SET snapshot_json = ? WHERE id = ?",
                    (db.dumps(snapshot), activation_id),
                )
            await self.config_service.apply_ops(plan["ops"])
            applied = True
            self._verify(account_id, manifest, plan)
            await self._record_objects(bundle_id, account_id, plan["snapshot"]["created"])
            await db.execute(
                "INSERT OR REPLACE INTO account_active_bundle "
                "(account_id, bundle_id, activation_id, updated_at) VALUES (?,?,?,?)",
                (account_id, bundle_id, activation_id, int(time.time())),
            )
            await db.execute(
                "UPDATE bundle_activations SET status = ?, finished_at = ?, applied_json = ? WHERE id = ?",
                ("active", int(time.time()), db.dumps(plan["applied"]), activation_id),
            )
            # 升级：旧激活标记为 superseded（不能再被回滚/卸载重复恢复），
            # 再删除上一版本创建且已无人引用的对象
            if previous is not None and prev_row is not None:
                await db.execute(
                    "UPDATE bundle_activations SET status = ? "
                    "WHERE bundle_id = ? AND account_id = ? AND status = ? AND id != ?",
                    ("superseded", bundle_id, str(account_id), "active", activation_id),
                )
            if previous and previous.get("created"):
                # 只清理"这次勾选内容"上一版本创建的对象：部分应用不能把
                # 没勾的人格/模型当成被替代对象删掉
                kind_sections = {
                    "role_preset": "persona",
                    "provider_preset": "provider",
                    "provider_model": "provider",
                }
                superseded = [
                    item for item in previous["created"]
                    if kind_sections.get(str(item.get("kind"))) in selected
                ]
                await self._cleanup_superseded(superseded, plan["snapshot"]["created"])
        except Exception as e:  # noqa: BLE001 —— 失败要留痕并可回滚，不往上抛原始栈
            # apply_ops 自身是原子的：它抛错时内存/DB 已回到应用前，不必再"恢复"；
            # 只有"已写入但后续步骤失败（校验/记账）"才需要按快照回滚。
            if applied:
                try:
                    await self._restore(plan["snapshot"])
                except Exception as restore_error:  # noqa: BLE001 —— 恢复失败要留痕，但不能掩盖原始错误
                    self.config_service.log.error(
                        f"[Bundle] 应用失败后回滚也失败，需人工检查: {restore_error}"
                    )
            await self._uninstall_modules(installed)
            await db.execute(
                "UPDATE bundle_activations SET status = ?, error = ?, finished_at = ? WHERE id = ?",
                ("failed", str(e), int(time.time()), activation_id),
            )
            raise BundleError("BUNDLE_APPLY_FAILED", f"应用到账号失败（已回滚）：{e}") from e

        return {
            "activation_id": activation_id,
            "status": "active",
            "plan": plan_public,
            "modules_installed": installed,
            "modules_upgraded": upgraded,
            "modules_deps": deps_log,
        }

    async def _cleanup_superseded(self, previous: list[dict], current: list[dict]) -> list[str]:
        """升级后清理上一版本创建、且不再被任何账号引用的对象（用户后建的不在此列）。"""
        keep = {item["id"] for item in current}
        removed: list[str] = []
        agent_configs = self.config_service.get_all_module_configs("agent")
        pool_ids = {
            str(item)
            for config in agent_configs.values()
            for item in (config.get("provider_model_pool") or [])
        }
        preset_ids = {str(config.get("provider_preset_id", "")) for config in agent_configs.values()}
        model_ids = {str(config.get("provider_model_id", "")) for config in agent_configs.values()}
        persona_ids = {str(config.get("persona_preset_id", "")) for config in agent_configs.values()}
        models = self.config_service.list_provider_models()
        for item in previous:
            kind, object_id = item["kind"], item["id"]
            if object_id in keep:
                continue
            referenced = False
            if kind == "role_preset":
                referenced = object_id in persona_ids
            elif kind == "provider_model":
                referenced = object_id in model_ids or object_id in pool_ids
            elif kind == "provider_preset":
                referenced = object_id in preset_ids or any(
                    str(model.get("preset_id", "")) == object_id for model in models
                )
            if referenced:
                continue
            await self.config_service.apply_ops([ConfigOp(kind, (object_id,), None)])
            await self.config_service.db.execute(
                "DELETE FROM bundle_objects WHERE kind = ? AND object_id = ?", (kind, object_id)
            )
            removed.append(f"{kind}:{object_id}")
        return removed

    def _verify(self, account_id: str, manifest: dict, plan: dict) -> None:
        """回读校验：应用后的值必须等于计划（否则视为失败并回滚）。"""
        row = self.config_service.get_binding(AGENT_CAPABILITY, "account", account_id) or {}
        config = row.get("config") or {}
        for op in plan["ops"]:
            if op.kind != "binding" or op.key[0] != AGENT_CAPABILITY or not op.value:
                continue
            expected_config = op.value.get("config") or {}
            for key in ("persona_preset_id", "provider_preset_id", "provider_model_id"):
                expected = expected_config.get(key)
                if expected is not None and config.get(key) != expected:
                    raise BundleError("BUNDLE_VERIFY_FAILED", f"校验失败：{key} 未生效")

    async def _record_objects(self, bundle_id: str, account_id: str, created: list[dict]) -> None:
        db = self.config_service.db
        for item in created:
            await db.execute(
                "INSERT OR REPLACE INTO bundle_objects (bundle_id, kind, object_id, account_id, created_at) "
                "VALUES (?,?,?,?,?)",
                (bundle_id, item["kind"], item["id"], account_id, int(time.time())),
            )

    # ==================== 回滚 ====================

    async def rollback(self, activation_id: str, *, keep_modules: bool = False) -> dict:
        """回滚一次激活。

        ``keep_modules=True``：只回滚配置、**保留**本次新装的模块（模块来源台账也保留），
        用于"卸载预设但想继续用这些插件"的场景。
        """
        db = self.config_service.db
        row = await db.fetchone("SELECT * FROM bundle_activations WHERE id = ?", (activation_id,))
        if row is None:
            raise BundleError("ACTIVATION_NOT_FOUND", f"激活记录不存在：{activation_id}")
        snapshot = self.config_service.db.loads(row["snapshot_json"], {}) or {}
        if row["status"] == "rolled_back":
            return {"activation_id": activation_id, "status": "rolled_back", "restored": []}

        restored = await self._restore(snapshot)
        removed_modules = (
            []
            if keep_modules
            else await self._uninstall_modules(snapshot.get("modules_installed") or [])
        )
        await db.execute(
            "UPDATE bundle_activations SET status = ?, finished_at = ? WHERE id = ?",
            ("rolled_back", int(time.time()), activation_id),
        )
        await db.execute(
            "DELETE FROM account_active_bundle WHERE account_id = ? AND activation_id = ?",
            (row["account_id"], activation_id),
        )
        return {
            "activation_id": activation_id,
            "status": "rolled_back",
            "restored": restored,
            "modules_uninstalled": removed_modules,
            "modules_kept": list(snapshot.get("modules_installed") or []) if keep_modules else [],
        }

    async def _restore(self, snapshot: dict) -> list[str]:
        """按快照恢复：删除本预设创建的对象，并把绑定写回应用前的值。"""
        ops: list[ConfigOp] = []
        restored: list[str] = []
        for item in snapshot.get("created") or []:
            ops.append(ConfigOp(item["kind"], (item["id"],), None))
            restored.append(f"{item['kind']}:{item['id']}")
        for binding in snapshot.get("bindings") or []:
            capability = binding["capability"]
            ops.append(ConfigOp(
                "binding", (capability, binding["scope_type"], binding["scope_id"]),
                binding.get("row"),
            ))
            restored.append(f"binding:{capability}")
        if ops:
            await self.config_service.apply_ops(ops)
        return restored

    # ==================== 恢复（进程崩溃） ====================

    async def recover_interrupted(self) -> list[str]:
        """把启动前遗留的 `applying` 记录回滚（幂等；返回被回滚的 activation id）。"""
        db = self.config_service.db
        rows = await db.fetchall(
            "SELECT id FROM bundle_activations WHERE status = ?", ("applying",)
        )
        recovered: list[str] = []
        for row in rows or []:
            try:
                await self.rollback(row["id"])
                recovered.append(row["id"])
            except Exception as e:  # noqa: BLE001 —— 单条恢复失败不能拖垮启动
                self.config_service.log.error(f"[Bundle] 自动回滚失败 {row['id']}: {e}")
        return recovered
