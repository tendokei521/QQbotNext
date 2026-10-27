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


def satisfies_version(version: str, constraint: str) -> bool:
    """极简版本约束：``""``/``*`` 恒真；支持 ``>=x.y.z``、``<=``、``==``、``>``、``<``（逗号并列）。"""
    text = str(constraint or "").strip()
    if not text or text in ("*", "any"):
        return True
    current = parse_semver(version)
    if current is None:
        return False
    for index, part in enumerate(piece.strip() for piece in text.split(",")):
        if not part:
            continue
        match = re.match(r"^(>=|<=|==|>|<|=)?\s*v?(\d+)\.(\d+)\.(\d+)", part)
        if not match:
            return False
        op, *numbers = match.groups()
        target = (int(numbers[0]), int(numbers[1]), int(numbers[2]))
        if op in (">=", None, "", "=") and index == 0 and not op:
            op = ">="
        if op == ">=" and not current >= target:
            return False
        if op == "<=" and not current <= target:
            return False
        if op == ">" and not current > target:
            return False
        if op == "<" and not current < target:
            return False
        if op in ("==", "=") and current != target:
            return False
    return True


def _require(condition: bool, code: str, message: str, hint: str = "") -> None:
    if not condition:
        raise BundleError(code, message, hint)


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _name_list(value: Any) -> list[str] | None:
    """工具/技能名列表：``None`` = 不限制（保持 None 语义），其余归一成去重字符串列表。"""
    if value is None:
        return None
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return None
    names = [str(item).strip() for item in value if str(item or "").strip()]
    return list(dict.fromkeys(names))


def _begin_dialogs(value: Any) -> list[dict]:
    """预设对话：只保留 ``{role: user|assistant, content: 非空}``。"""
    result: list[dict] = []
    for item in _as_list(value):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        result.append({"role": role, "content": content})
    return result


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
            # 随包模块的 zip 路径（包内相对路径，空 = 只声明依赖、不带代码）
            "source": str(item.get("source") or "").strip(),
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
            # 预设的扩展字段（提示词全文在 source 里；这些是"角色卡"的其余部分）
            "description": str(item.get("description") or "").strip(),
            "begin_dialogs": _begin_dialogs(item.get("begin_dialogs")),
            "tools": _name_list(item.get("tools")),
            "skills": _name_list(item.get("skills")),
        })

    providers: list[dict] = []
    for item in _as_list(components.get("providers")):
        if not isinstance(item, dict):
            continue
        api_base = str(item.get("api_base") or "").strip()
        models: list[dict] = []
        for model_item in _as_list(item.get("models")):
            if not isinstance(model_item, dict):
                continue
            model_name = str(model_item.get("model") or "").strip()
            if not model_name:
                continue
            models.append({
                "model": model_name,
                "provider_type": str(model_item.get("provider_type") or "chat").strip() or "chat",
                "enabled": bool(model_item.get("enabled", True)),
                "config": _as_dict(model_item.get("config")),
            })
        model = str(item.get("model") or "").strip()
        if not model and models:
            model = models[0]["model"]
        if not api_base or not model:
            continue
        _require(
            api_base.startswith(("http://", "https://")),
            "BUNDLE_MANIFEST_INVALID",
            f"provider.api_base 必须是 http(s) URL：{api_base}",
        )
        if not models:
            models = [{
                "model": model, "provider_type": "chat", "enabled": True, "config": {},
            }]
        providers.append({
            "provider": str(item.get("provider") or "openai").strip() or "openai",
            "api_base": api_base.rstrip("/"),
            "model": model,
            "preset_name": str(item.get("preset_name") or "").strip(),
            "apply_to": str(item.get("apply_to") or "agent.provider_model_pool"),
            # 连接预设的其余字段（api_base/api_key 之外，如重试次数）；密钥走 secrets
            "config": _as_dict(item.get("config")),
            # 模型池全量迁移（v1 曾只带第一个，导致多模型池丢配置）
            "models": models,
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
            "version": str(item.get("version") or "").strip(),
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
