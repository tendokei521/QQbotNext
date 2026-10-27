"""套装的导出：把"某个账号的当前配置"打包成工件。

三条硬规矩（设计 §5.3 / §6.9）：
1. **密钥永不进包**：识别出的密钥字段被剥离，转成清单里的 ``secrets`` 声明；
2. **人格全文进包**，但 ``persona_preset_id`` 这类活引用被清掉（导入时重建）；
3. **确定性输出**：文件顺序与时间戳固定，同样的配置导出两次字节一致（便于 hash/对比）。
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
    # 固定时间戳，保证同输入 → 同字节
    fixed = (2020, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=fixed)
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
    notes: dict | None = None,
) -> bytes:
    """组装 zip 字节（不落盘）。

    Args:
        agent_config: 账号的 Agent 配置（会被剔除活引用与密钥）。
        persona: ``{"name": str, "text": str}``（人格全文）。
        provider: ``{"provider","api_base","model","preset_name","api_key"?}``。
        module_configs: ``{module_name: {"enabled": bool, "config": dict}}``。
    """
    files: dict[str, bytes] = {}
    secrets: list[dict] = []
    components: dict[str, list] = {"personas": [], "providers": [], "agent": [], "modules": []}

    if persona and str(persona.get("text") or "").strip():
        files["persona/default.md"] = str(persona["text"]).strip().encode("utf-8")
        components["personas"].append({
            "source": "persona/default.md",
            "name": str(persona.get("name") or "套装人格"),
            "apply_to": "agent.persona_preset_id",
        })

    if provider and provider.get("api_base") and provider.get("model"):
        provider_secret = str(provider.get("api_key") or "")
        if provider_secret:
            secrets.append({
                "key": "providers[0].api_key",
                "label": f"{provider.get('provider') or 'provider'} API Key",
                "required": True,
                "hint": "导入时重新填写（密钥不随包分发）",
            })
        components["providers"].append({
            "provider": str(provider.get("provider") or "openai"),
            "api_base": str(provider["api_base"]).rstrip("/"),
            "model": str(provider["model"]),
            "preset_name": str(provider.get("preset_name") or ""),
            "apply_to": "agent.provider_model_pool",
        })

    if agent_config:
        cleaned = {
            key: value for key, value in agent_config.items()
            if key not in AGENT_REFERENCE_KEYS
        }
        cleaned, agent_secrets, _agent_values = strip_secrets(cleaned, prefix="agent")
        secrets.extend(agent_secrets)
        files["configs/agent.json"] = json.dumps(cleaned, ensure_ascii=False, indent=2).encode("utf-8")
        components["agent"].append({"source": "configs/agent.json", "merge": "keys"})

    for module_name, entry in (module_configs or {}).items():
        enabled = bool((entry or {}).get("enabled", True))
        config = (entry or {}).get("config") or {}
        if config:
            cleaned_config, module_secrets, _values = strip_secrets(
                config, prefix=f"module:{module_name}"
            )
            secrets.extend(module_secrets)
            files[f"configs/module-{module_name}.json"] = json.dumps(
                cleaned_config, ensure_ascii=False, indent=2
            ).encode("utf-8")
        components["modules"].append({
            "name": module_name,
            "enabled": enabled,
            "source": f"configs/module-{module_name}.json" if config else "",
        })

    readme = "\n".join(filter(None, [
        f"# {name}",
        "",
        description or "由 QQBot Next 导出的配置套装。",
        "",
        "## 导入说明",
        "- 导入后请按提示填写密钥（密钥不随包分发）。",
        "- 应用前会先预检，确认模块/版本/引用都满足。",
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
        "requires": {"modules": [
            {"name": module["name"], "version": "", "required": False}
            for module in components["modules"]
        ]},
        "components": components,
        "secrets": secrets,
        "notes": {
            "intro": "导入后请先预检；密钥需要重新填写。",
            "changelog": (notes or {}).get("changelog", ""),
        },
    }
    files["bundle.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    files["checksums.sha256"] = _checksums(files)
    return _write_zip(files)
