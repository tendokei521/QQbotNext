"""预设校验器：结构 → 清单 → 兼容 → 依赖 → 引用 → 安全。

输出「预检报告」而不是只抛异常：前端要展示"将创建什么 / 缺什么 / 需手填什么"。
结构性问题（不是 zip / 路径穿越 / 清单缺失）仍然抛 :class:`BundleError`（400），
语义问题（模块缺失、密钥泄漏、未知键）进 ``issues``，由调用方决定是否继续。
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile

from app.application.bundle.errors import BundleError
from app.application.bundle.manifest import parse_semver, satisfies_version
from app.application.bundle.module_packages import read_module_identity
from app.domain.dialogue_policy import validate_override_values  # noqa: F401  (预留：P4 值校验复用)

#: 解压上限（与插件安装器同量级；按"解压后真实大小"统计，不信任 zip 头）
MAX_TOTAL_BYTES = 200 * 1024 * 1024
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_FILES = 5000

_SECRET_RE = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")
_SECRET_FIELD_RE = re.compile(r'"(?:api_key|access_token|secret|password)"\s*:\s*"[^"]{4,}"')

#: 清单本身允许的组件来源文件（用于"文件必须存在"检查）
_TEXT_SUFFIXES = (".md", ".txt", ".json")


def _issue(level: str, code: str, title: str, detail: str = "", fix: str = "") -> dict:
    return {"level": level, "code": code, "title": title, "detail": detail, "fix": fix}


def _safe_parts(name: str) -> list[str]:
    """拒绝路径穿越 / 绝对路径 / 盘符（与插件安装器同一口径）。"""
    text = str(name or "").replace("\\", "/")
    if not text or text.startswith("/") or ".." in text.split("/"):
        raise BundleError("BUNDLE_ZIP_UNSAFE", f"压缩包内含不安全路径：{name}")
    parts = [part for part in text.split("/") if part]
    if parts and (len(parts[0]) == 2 and parts[0][1] == ":"):
        raise BundleError("BUNDLE_ZIP_UNSAFE", f"压缩包内含盘符路径：{name}")
    return parts


def inspect_zip(data: bytes) -> tuple[dict, dict, dict]:
    """解析 zip 字节 → ``(manifest_raw, files, checksums)``。

    - 按**实际解压大小**统计上限（不信任中央目录声明）；
    - 拒绝符号链接、路径穿越、盘符；
    - ``bundle.json`` 必须在根；``checksums.sha256`` 可选，存在则逐文件校验。
    """
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise BundleError("BUNDLE_ZIP_INVALID", "空文件不是有效的预设包")
    if len(data) > MAX_TOTAL_BYTES:
        raise BundleError("BUNDLE_ZIP_TOO_LARGE", "预设包超过 200 MB")

    files: dict[str, bytes] = {}
    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(bytes(data))) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_FILES:
                raise BundleError("BUNDLE_ZIP_TOO_LARGE", f"文件数超过 {MAX_FILES}")
            for info in infos:
                if info.is_dir():
                    continue
                parts = _safe_parts(info.filename)
                if not parts:
                    continue
                if info.external_attr >> 16 & 0o170000 == 0o120000:
                    raise BundleError("BUNDLE_ZIP_UNSAFE", f"不允许符号链接：{info.filename}")
                payload = archive.read(info)
                total += len(payload)
                if len(payload) > MAX_FILE_BYTES:
                    raise BundleError("BUNDLE_ZIP_TOO_LARGE", f"单文件超过 50 MB：{info.filename}")
                if total > MAX_TOTAL_BYTES:
                    raise BundleError("BUNDLE_ZIP_TOO_LARGE", "解压后超过 200 MB")
                files["/".join(parts)] = payload
    except BundleError:
        raise
    except (zipfile.BadZipFile, RuntimeError, OSError) as e:
        raise BundleError("BUNDLE_ZIP_INVALID", f"不是有效的 zip：{e}") from e

    if "bundle.json" not in files:
        raise BundleError("BUNDLE_MANIFEST_MISSING", "预设包根目录缺少 bundle.json", "请确认打包时把清单放在根目录")

    checksums: dict[str, str] = {}
    checksum_bytes = files.get("checksums.sha256")
    if checksum_bytes is not None:
        for line in checksum_bytes.decode("utf-8", "replace").splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2:
                checksums[parts[1].replace("\\", "/").lstrip("./")] = parts[0].lower()
        for path, expected in checksums.items():
            if path not in files:
                raise BundleError("BUNDLE_CHECKSUM_MISMATCH", f"清单声明的文件不存在：{path}")
            actual = hashlib.sha256(files[path]).hexdigest()
            if actual != expected:
                raise BundleError("BUNDLE_CHECKSUM_MISMATCH", f"文件校验失败：{path}")

    import json

    try:
        manifest_raw = json.loads(files["bundle.json"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise BundleError("BUNDLE_MANIFEST_INVALID", f"bundle.json 不是合法 JSON：{e}") from e
    return manifest_raw, files, checksums


def validate_bytes(
    data: bytes,
    *,
    framework_api_version: int = 1,
    framework_version: str = "2.0.0",
    installed_modules: dict[str, str] | None = None,
    agent_schema: dict | None = None,
) -> dict:
    """产出一份预检报告（结构性问题抛 BundleError，语义问题进 issues）。"""
    from app.application.bundle.manifest import normalize_manifest

    manifest_raw, files, _checksums = inspect_zip(data)
    manifest = normalize_manifest(manifest_raw)
    issues: list[dict] = []

    # ---------- 兼容性 ----------
    if manifest["framework"]["api_version"] > int(framework_api_version):
        issues.append(_issue(
            "error", "BUNDLE_INCOMPATIBLE",
            "包要求的框架 API 版本更高",
            f"包要求 {manifest['framework']['api_version']}，当前 {framework_api_version}",
            "升级框架后再导入",
        ))
    min_version = manifest["framework"]["min_version"]
    current = parse_semver(framework_version)
    if min_version and current is not None:
        wanted = parse_semver(min_version)
        if wanted is not None and current < wanted:
            issues.append(_issue(
                "error", "BUNDLE_INCOMPATIBLE",
                f"需要框架 ≥ {min_version}", f"当前 {framework_version}", "升级框架后再导入",
            ))

    # ---------- 模块依赖 ----------
    installed = {str(k): str(v or "0.0.0") for k, v in (installed_modules or {}).items()}
    # 随包模块（zip 代码包）：先验包身份，再参与依赖判定（缺它不等于缺模块）
    bundled: dict[str, dict] = {}
    for required in manifest["requires"]["modules"]:
        source = required["source"]
        if not source:
            continue
        payload = files.get(source)
        if payload is None:
            issues.append(_issue(
                "error", "BUNDLE_REF_MISSING", f"模块代码包不存在：{source}",
                "", "重新导出或让提供方补上 modules/ 目录",
            ))
            continue
        try:
            identity = read_module_identity(payload)
        except BundleError as e:
            issues.append(_issue(
                "error", "BUNDLE_MODULE_PACKAGE_INVALID",
                f"模块代码包不可用：{source}", str(e), e.hint,
            ))
            continue
        if identity["name"] != required["name"]:
            issues.append(_issue(
                "error", "BUNDLE_MODULE_PACKAGE_INVALID",
                f"模块代码包与声明不一致：{source}",
                f"包内是 {identity['name']}，清单声明 {required['name']}",
                "检查导出时的模块打包逻辑",
            ))
            continue
        if required["version"] and not satisfies_version(identity["version"], required["version"]):
            issues.append(_issue(
                "error", "BUNDLE_MODULE_PACKAGE_INVALID",
                f"模块代码包版本不满足：{source}",
                f"包内 {identity['version'] or '(空)'}，要求 {required['version']}",
            ))
            continue
        bundled[required["name"]] = identity

    will_install: list[str] = []
    for required in manifest["requires"]["modules"]:
        name = required["name"]
        if name not in installed:
            if name in bundled:
                will_install.append(name)
                continue
            if required["required"]:
                issues.append(_issue(
                    "error", "BUNDLE_MODULE_MISSING", f"缺少模块 {name}", "未安装且未随包", "先安装该模块再导入",
                ))
            else:
                issues.append(_issue(
                    "warn", "BUNDLE_MODULE_OPTIONAL_MISSING", f"可选模块 {name} 未安装", "将跳过其配置",
                ))
            continue
        if required["version"] and not satisfies_version(installed[name], required["version"]):
            if name in bundled:
                will_install.append(name)
                issues.append(_issue(
                    "warn", "BUNDLE_MODULE_UPGRADE",
                    f"模块 {name} 将随包升级",
                    f"已装 {installed[name]}，包内 {bundled[name]['version']}",
                ))
            else:
                issues.append(_issue(
                    "error", "BUNDLE_MODULE_VERSION", f"模块 {name} 版本不满足",
                    f"已装 {installed[name]}，要求 {required['version']}", "升级模块后再导入",
                ))

    # 模块清单（预检页逐条展示：名称/版本/是否随包/动作/体积）
    modules_detail: list[dict] = []
    for required in manifest["requires"]["modules"]:
        name = required["name"]
        identity = bundled.get(name)
        package_size = 0
        source = required["source"]
        if source and source in files:
            package_size = len(files[source])
        if name in will_install:
            action = "upgrade" if name in installed else "install"
        elif name in installed:
            action = "skip"
        elif identity is None:
            action = "missing"
        else:
            action = "install"
        modules_detail.append({
            "name": name,
            "version": required["version"],
            "required": required["required"],
            "bundled": identity is not None,
            "package_version": identity["version"] if identity else "",
            "package_size": package_size,
            "installed_version": installed.get(name, ""),
            "action": action,
        })
    declared = {item["name"] for item in manifest["requires"]["modules"]}
    for component in manifest["components"]["modules"]:
        if component["name"] in declared:
            continue
        modules_detail.append({
            "name": component["name"],
            "version": component.get("version") or "",
            "required": False,
            "bundled": False,
            "package_version": "",
            "package_size": 0,
            "installed_version": installed.get(component["name"], ""),
            "action": "config_only",
        })

    # ---------- 引用完整性 ----------
    for persona in manifest["components"]["personas"]:
        if persona["source"] not in files:
            issues.append(_issue("error", "BUNDLE_REF_MISSING", f"人格文件不存在：{persona['source']}"))
    for agent in manifest["components"]["agent"]:
        if agent["source"] not in files:
            issues.append(_issue("error", "BUNDLE_REF_MISSING", f"Agent 配置文件不存在：{agent['source']}"))
    for module in manifest["components"]["modules"]:
        if module["source"] and module["source"] not in files:
            issues.append(_issue("error", "BUNDLE_REF_MISSING", f"模块配置文件不存在：{module['source']}"))

    # ---------- Agent 配置键校验（未知键 = 装进去也不会生效） ----------
    if agent_schema:
        import json

        for agent in manifest["components"]["agent"]:
            payload = files.get(agent["source"])
            if payload is None:
                continue
            try:
                data_obj = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                issues.append(_issue("error", "BUNDLE_VALUE_INVALID", f"{agent['source']} 不是合法 JSON"))
                continue
            if not isinstance(data_obj, dict):
                issues.append(_issue("error", "BUNDLE_VALUE_INVALID", f"{agent['source']} 必须是对象"))
                continue
            unknown = sorted(key for key in data_obj if key not in agent_schema)
            if unknown:
                issues.append(_issue(
                    "error", "BUNDLE_KEY_UNKNOWN",
                    f"{agent['source']} 含未知配置键", "、".join(unknown[:10]) + ("…" if len(unknown) > 10 else ""),
                    "检查包是否由匹配版本的框架导出",
                ))
            # 值校验（P4-2）：类型/范围/枚举写错，导入后同样会静默失效
            from app.plugin.value_validator import validate_config

            for problem in validate_config(agent_schema, data_obj):
                issues.append(_issue(
                    "error", "BUNDLE_VALUE_INVALID",
                    f"{agent['source']} 的 {problem['key']} 不合法", problem["message"],
                ))

    # ---------- 密钥泄漏扫描 ----------
    for path, payload in files.items():
        if not path.endswith(_TEXT_SUFFIXES):
            continue
        text = payload.decode("utf-8", "replace")
        if path == "bundle.json":
            # 清单里的 secrets 只是声明，不该出现真实值
            for secret in manifest["secrets"]:
                if secret["key"] and secret["key"] in text and _SECRET_RE.search(text):
                    issues.append(_issue("error", "BUNDLE_SECRET_LEAK", "清单里疑似包含真实密钥"))
                    break
            continue
        if _SECRET_RE.search(text) or _SECRET_FIELD_RE.search(text):
            issues.append(_issue(
                "error", "BUNDLE_SECRET_LEAK",
                f"内容物疑似包含明文密钥：{path}", "", "导出时不要包含 Key；接收方导入后自行填写",
            ))

    # ---------- 汇总 ----------
    secrets_required = [secret for secret in manifest["secrets"] if secret["required"]]
    summary = {
        "modules": len(manifest["components"]["modules"]),
        "presets": len(manifest["components"]["personas"]) + len(manifest["components"]["providers"]),
        "agent_keys": len(manifest["components"]["agent"]),
        "files": len(files),
        "size": len(data),
        "secrets_required": len(secrets_required),
        # 随包模块：代码随包的模块数 / 应用时需要安装的模块数
        "modules_bundled": len(bundled),
        "modules_to_install": len(will_install),
    }
    errors = [issue for issue in issues if issue["level"] == "error"]
    return {
        "ok": not errors,
        "bundle": {
            "id": manifest["id"],
            "name": manifest["name"],
            "version": manifest["version"],
            "description": manifest["description"],
            "author": manifest["author"],
        },
        "summary": summary,
        "modules": modules_detail,
        "issues": issues,
        "secrets": manifest["secrets"],
        "manifest": {key: value for key, value in manifest.items() if key != "raw"},
    }
