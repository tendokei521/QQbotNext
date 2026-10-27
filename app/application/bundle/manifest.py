"""`bundle.json` 的解析与归一化（唯一清单格式）。

字段表见 `docs/oneclick-bundle-and-onboarding-design.md` §5.2。这里只做"清单本身"
的强校验（类型/必填/格式），跨对象检查（模块是否装了、文件是否存在）由 validator 做。
"""

from __future__ import annotations

import re
from typing import Any

from app.application.bundle.errors import BundleError

#: 包格式版本；不兼容的旧/新包直接拒绝
BUNDLE_SCHEMA = "qqbotnext.bundle/1"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?$")


def parse_semver(value: str) -> tuple[int, int, int] | None:
    match = _SEMVER_RE.match(str(value or "").strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _require(condition: bool, code: str, message: str, hint: str = "") -> None:
    if not condition:
        raise BundleError(code, message, hint)


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def normalize_manifest(raw: Any) -> dict:
    """把 `bundle.json` 归一化成稳定形状；不合法时抛 :class:`BundleError`。"""
    _require(isinstance(raw, dict), "BUNDLE_MANIFEST_INVALID", "bundle.json 必须是 JSON 对象")

    schema = str(raw.get("schema") or "").strip()
    _require(
        schema == BUNDLE_SCHEMA,
        "BUNDLE_SCHEMA_UNSUPPORTED",
        f"不支持的包格式：{schema or '(空)'}（本版本只认 {BUNDLE_SCHEMA}）",
        "请用与当前框架版本匹配的打包工具导出",
    )

    bundle_id = str(raw.get("id") or "").strip()
    _require(
        bool(_ID_RE.match(bundle_id)),
        "BUNDLE_ID_INVALID",
        f"套装 id 不合法：{bundle_id or '(空)'}（只允许小写字母/数字/.-_，3–64 位）",
    )

    name = str(raw.get("name") or "").strip()
    _require(bool(name), "BUNDLE_MANIFEST_INVALID", "缺少套装名称（name）")
    _require(len(name) <= 100, "BUNDLE_MANIFEST_INVALID", "套装名称过长（≤100 字）")

    version = str(raw.get("version") or "").strip()
    _require(
        parse_semver(version) is not None,
        "BUNDLE_VERSION_INVALID",
        f"版本号不是 semver：{version or '(空)'}",
    )

    framework = _as_dict(raw.get("framework"))
    api_version = framework.get("api_version", 1)
    _require(
        isinstance(api_version, int) and api_version >= 1,
        "BUNDLE_MANIFEST_INVALID",
        "framework.api_version 必须是正整数",
    )

    requires = _as_dict(raw.get("requires"))
    modules: list[dict] = []
    for item in _as_list(requires.get("modules")):
        if not isinstance(item, dict):
            continue
        module_name = str(item.get("name") or "").strip()
        if not module_name:
            continue
        modules.append({
            "name": module_name,
            "version": str(item.get("version") or "").strip(),
            "required": bool(item.get("required", True)),
        })

    components = _as_dict(raw.get("components"))
    personas: list[dict] = []
    for item in _as_list(components.get("personas")):
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        if not source:
            continue
        personas.append({
            "source": source,
            "name": str(item.get("name") or "").strip() or "套装人格",
            "apply_to": str(item.get("apply_to") or "agent.persona_preset_id"),
        })

    providers: list[dict] = []
    for item in _as_list(components.get("providers")):
        if not isinstance(item, dict):
            continue
        api_base = str(item.get("api_base") or "").strip()
        model = str(item.get("model") or "").strip()
        if not api_base or not model:
            continue
        _require(
            api_base.startswith(("http://", "https://")),
            "BUNDLE_MANIFEST_INVALID",
            f"provider.api_base 必须是 http(s) URL：{api_base}",
        )
        providers.append({
            "provider": str(item.get("provider") or "openai").strip() or "openai",
            "api_base": api_base.rstrip("/"),
            "model": model,
            "preset_name": str(item.get("preset_name") or "").strip(),
            "apply_to": str(item.get("apply_to") or "agent.provider_model_pool"),
        })

    agent_configs: list[dict] = []
    for item in _as_list(components.get("agent")):
        if isinstance(item, str):
            item = {"source": item}
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        if source:
            agent_configs.append({"source": source, "merge": str(item.get("merge") or "keys")})

    component_modules: list[dict] = []
    for item in _as_list(components.get("modules")):
        if not isinstance(item, dict):
            continue
        module_name = str(item.get("name") or "").strip()
        if not module_name:
            continue
        component_modules.append({
            "name": module_name,
            "enabled": bool(item.get("enabled", True)),
            "source": str(item.get("source") or "").strip(),
        })

    secrets: list[dict] = []
    for item in _as_list(raw.get("secrets")):
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        secrets.append({
            "key": key,
            "label": str(item.get("label") or "").strip() or key,
            "required": bool(item.get("required", True)),
            "hint": str(item.get("hint") or "").strip(),
        })

    notes = _as_dict(raw.get("notes"))
    return {
        "schema": BUNDLE_SCHEMA,
        "id": bundle_id,
        "name": name,
        "version": version,
        "author": _as_dict(raw.get("author")),
        "description": str(raw.get("description") or "").strip()[:200],
        "license": str(raw.get("license") or "").strip(),
        "framework": {
            "api_version": api_version,
            "min_version": str(framework.get("min_version") or "").strip(),
        },
        "schema_versions": {
            str(key): int(value)
            for key, value in _as_dict(raw.get("schema_versions")).items()
            if isinstance(value, int)
        },
        "requires": {"modules": modules},
        "components": {
            "personas": personas,
            "providers": providers,
            "agent": agent_configs,
            "modules": component_modules,
        },
        "secrets": secrets,
        "permissions": _as_dict(raw.get("permissions")),
        "notes": {
            "intro": str(notes.get("intro") or "").strip(),
            "changelog": str(notes.get("changelog") or "").strip(),
        },
        "raw": raw,
    }
