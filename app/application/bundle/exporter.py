"""预设的导出：把"某个账号的当前配置"打包成工件。

四条硬规矩（设计 §5.3 / §6.9 + 模块迁移）：

1. **密钥永不进包**：识别出的密钥字段被剥离，转成清单里的 ``secrets`` 声明；
2. **人格全文 + 角色卡字段进包**：提示词、描述、预设对话、工具/技能名都随包；
   活引用（``persona_preset_id`` 等）清掉，导入时重建；
3. **模块代码可随包**：第三方（zip 安装）模块打 ``modules/<name>-<ver>.zip``，
   内置模块只声明版本（随框架走，不重复打包）；
4. **确定性输出**：文件顺序与时间戳固定，同样的配置导出两次字节一致（便于 hash/对比）。
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from typing import Any

from app.application.bundle.manifest import BUNDLE_SCHEMA

#: 字段名命中即视为密钥（与 PASSWORD_MASK 的字段类同）
SECRET_KEY_HINTS = ("api_key", "access_token", "token", "secret", "password")
#: 导出时从 Agent 配置里剔除的"活引用/机器相关"键（导入时由目标机重建）
AGENT_REFERENCE_KEYS = (
    "persona_preset_id",      # 人格全文已随包导出（persona/default.md）
    "provider_preset_id",
    "provider_model_id",
    "provider_model_pool",
)

#: 固定 zip 时间戳，保证同输入 → 同字节
_FIXED_TIME = (2020, 1, 1, 0, 0, 0)


def _is_secret_key(key: str) -> bool:
    lowered = str(key).lower()
    return any(hint in lowered for hint in SECRET_KEY_HINTS)


def strip_secrets(config: dict[str, Any], *, prefix: str) -> tuple[dict[str, Any], list[dict], dict]:
    """剥离密钥字段：返回 ``(干净配置, secrets 声明, 建议值)``。

    ``prefix`` 用于生成 secrets 的 key 路径（如 ``agent`` / ``module:llm_memory``）。
    """
    clean: dict[str, Any] = {}
    secrets: list[dict] = []
    values: dict[str, Any] = {}
    for key, value in (config or {}).items():
        if _is_secret_key(key) and value:
            path = f"{prefix}.{key}"
            secrets.append({
                "key": path,
                "label": f"{prefix} · {key}",
                "required": True,
                "hint": "导入时重新填写（密钥不随包分发）",
            })
            values[path] = value
            continue
        clean[key] = value
    return clean, secrets, values


def _checksums(files: dict[str, bytes]) -> bytes:
    lines = []
    for name in sorted(files):
        if name == "checksums.sha256":
            continue
        lines.append(f"{hashlib.sha256(files[name]).hexdigest()}  {name}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _write_zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=_FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, files[name])
    return buffer.getvalue()


def export_bundle(
    *,
    bundle_id: str,
    name: str,
    version: str = "1.0.0",
    description: str = "",
    author: dict | None = None,
    agent_config: dict[str, Any] | None = None,
    persona: dict | None = None,
    provider: dict | None = None,
    module_configs: dict[str, dict] | None = None,
    module_packages: dict[str, bytes] | None = None,
    module_meta: dict[str, dict] | None = None,
    notes: dict | None = None,
) -> bytes:
    """组装 zip 字节（不落盘）。

    Args:
        agent_config: 账号的 Agent 配置（会被剔除活引用与密钥）。
        persona: ``{"name","text","description","begin_dialogs","tools","skills"}``。
        provider: 连接预设 + 模型池：
            ``{"provider","api_base","api_key","model","preset_name","config":{},
            "models":[{"model","provider_type","config","enabled"}]}``。
        module_configs: ``{module_name: {"enabled": bool, "config": dict}}``。
        module_packages: ``{module_name: zip 字节}``（仅第三方模块；内置不打包）。
        module_meta: ``{module_name: {"version": str, "builtin": bool}}``。
    """
    files: dict[str, bytes] = {}
    secrets: list[dict] = []
    components: dict[str, list] = {"personas": [], "providers": [], "agent": [], "modules": []}
    requires_modules: list[dict] = []
    module_packages = module_packages or {}
    module_meta = module_meta or {}
    skipped_packages: list[str] = []

    # ---------- 人格：提示词全文 + 角色卡字段 ----------
    if persona and str(persona.get("text") or "").strip():
        files["persona/default.md"] = str(persona["text"]).strip().encode("utf-8")
        components["personas"].append({
            "source": "persona/default.md",
            "name": str(persona.get("name") or "预设人格"),
            "apply_to": "agent.persona_preset_id",
            "description": str(persona.get("description") or "").strip(),
            "begin_dialogs": list(persona.get("begin_dialogs") or []),
            "tools": persona.get("tools"),
            "skills": persona.get("skills"),
        })

    # ---------- 模型连接：连接预设（脱敏）+ 模型池全量 ----------
    if provider and provider.get("api_base") and provider.get("model"):
        api_key = str(provider.get("api_key") or "")
        provider_config = dict(provider.get("config") or {})
        provider_config["api_base"] = str(provider["api_base"]).rstrip("/")
        provider_config, provider_secrets, _provider_values = strip_secrets(
            provider_config, prefix="providers[0]"
        )
        secrets.extend(provider_secrets)
        if api_key:
            secrets.append({
                "key": "providers[0].api_key",
                "label": f"{provider.get('provider') or 'provider'} API Key",
                "required": True,
                "hint": "导入时重新填写（密钥不随包分发）",
            })

        models_out: list[dict] = []
        for index, model in enumerate(provider.get("models") or []):
            if not isinstance(model, dict) or not str(model.get("model") or "").strip():
                continue
            model_config, model_secrets, _values = strip_secrets(
                dict(model.get("config") or {}), prefix=f"provider_models[{index}]"
            )
            secrets.extend(model_secrets)
            models_out.append({
                "model": str(model["model"]).strip(),
                "provider_type": str(model.get("provider_type") or "chat"),
                "enabled": bool(model.get("enabled", True)),
                "config": model_config,
            })
        if not models_out:
            models_out = [{
                "model": str(provider["model"]), "provider_type": "chat",
                "enabled": True, "config": {},
            }]

        components["providers"].append({
            "provider": str(provider.get("provider") or "openai"),
            "api_base": str(provider["api_base"]).rstrip("/"),
            "model": models_out[0]["model"],
            "preset_name": str(provider.get("preset_name") or ""),
            "apply_to": "agent.provider_model_pool",
            "config": provider_config,
            "models": models_out,
        })

    # ---------- Agent 配置子集 ----------
    if agent_config:
        cleaned = {
            key: value for key, value in agent_config.items()
            if key not in AGENT_REFERENCE_KEYS
        }
        cleaned, agent_secrets, _agent_values = strip_secrets(cleaned, prefix="agent")
        secrets.extend(agent_secrets)
        files["configs/agent.json"] = json.dumps(cleaned, ensure_ascii=False, indent=2).encode("utf-8")
        components["agent"].append({"source": "configs/agent.json", "merge": "keys"})

    # ---------- 模块：参数 + 启停 + 可选的代码包 ----------
    for module_name, entry in (module_configs or {}).items():
        enabled = bool((entry or {}).get("enabled", True))
        config = (entry or {}).get("config") or {}
        meta = module_meta.get(module_name) or {}
        module_version = str(meta.get("version") or "")
        builtin = bool(meta.get("builtin", True))
        source = ""
        if config:
            cleaned_config, module_secrets, _values = strip_secrets(
                config, prefix=f"module:{module_name}"
            )
            secrets.extend(module_secrets)
            files[f"configs/module-{module_name}.json"] = json.dumps(
                cleaned_config, ensure_ascii=False, indent=2
            ).encode("utf-8")
            source = f"configs/module-{module_name}.json"

        package_path = ""
        package = module_packages.get(module_name)
        if package:
            package_path = f"modules/{module_name}-{module_version or '0.0.0'}.zip"
            files[package_path] = package

        components["modules"].append({
            "name": module_name,
            "enabled": enabled,
            "source": source,
            "version": module_version,
        })
        requires_modules.append({
            "name": module_name,
            # 内置模块随框架走：只声明版本（低版本框架缺模块时给 warning，不阻断）
            "version": f">={module_version}" if module_version else "",
            "required": not builtin,
            "source": package_path,
        })
        if not builtin and not package:
            skipped_packages.append(module_name)

    readme = "\n".join(filter(None, [
        f"# {name}",
        "",
        description or "由 QQBot Next 导出的配置预设。",
        "",
        "## 导入说明",
        "- 导入后请按提示填写密钥（密钥不随包分发）。",
        "- 应用前会先预检，确认模块/版本/引用都满足；第三方模块代码随包时会在应用阶段安装。",
        ("- 以下模块未随包（体积超限或未找到目录），目标机需自行安装：" + "、".join(sorted(skipped_packages)))
        if skipped_packages else "",
        notes.get("changelog", "") if notes else "",
    ])) + "\n"
    files["README.md"] = readme.encode("utf-8")

    manifest = {
        "schema": BUNDLE_SCHEMA,
        "id": bundle_id,
        "name": name,
        "version": version,
        "author": author or {"name": "local"},
        "description": description,
        "framework": {"api_version": 1, "min_version": ""},
        "schema_versions": {"agent": 2, "module": 2},
        "requires": {"modules": requires_modules},
        "components": components,
        "secrets": secrets,
        "notes": {
            "intro": "导入后请先预检；密钥需要重新填写；第三方模块会在应用时自动安装。",
            "changelog": (notes or {}).get("changelog", ""),
        },
    }
    files["bundle.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    files["checksums.sha256"] = _checksums(files)
    return _write_zip(files)
