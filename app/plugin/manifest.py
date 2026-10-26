"""module.json v2 清单：模块的唯一元数据来源。

- 内置与第三方共用同一格式（对应 AstrBot 的 `metadata.yaml`），差别只在"缺失时怎么办"：
  内置缺清单 → WARN + 类属性兜底（框架核心模块不能因为一个元数据文件没写就装不进来）；
  第三方缺清单 → 直接拒绝（装进来也渲染不出名字/版本，属于坏包）。
- 安装期用它校验口径（必填字段 / 合法标识符 / api_version），加载期用它填
  `instance.version` / `instance.display_name` / `instance.manifest`。

规则见 docs/module-management-redesign.md §3.3。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: 清单文件名（固定，插件根下直接存在）
MANIFEST_FILENAME = "module.json"
#: 模块 id 同时要当目录名与合成包名，因此必须是合法 Python 标识符：
#: 不允许 "."（会被当层级）、":"（Windows 非法）、中文、空格。
NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
#: 框架当前支持的插件 API 版本集合（清单缺省视为 1）
SUPPORTED_API_VERSIONS = frozenset({1})
DEFAULT_API_VERSION = 1
DEFAULT_ENTRY = "main.py"
#: 清单体积上限（AstrBot 同样是 1MB 量级；这里只防"拿整个仓库当清单"）
MAX_MANIFEST_BYTES = 1024 * 1024


class ManifestError(ValueError):
    """清单不可用。`hint` 是给用户的修复建议，WebUI 会直接展示。"""

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


@dataclass(frozen=True)
class Manifest:
    """解析并校验后的清单（`raw` 保留原始 dict，便于回执/调试）。"""

    name: str
    display_name: str
    version: str
    description: str
    author: str
    entry: str
    api_version: int
    requires: tuple[str, ...]
    requirements: str | None
    raw: dict[str, Any]


def validate_manifest(data: dict, *, source: str) -> list[str]:
    """校验清单结构，返回问题列表（空列表 = 可用）。

    `source` 只影响错误信息口径，校验强度一致：内置与第三方都要求同一份必填字段。
    """
    if source not in ("builtin", "installed"):
        raise ValueError(f"未知的模块来源: {source!r}")
    label = "内置模块" if source == "builtin" else "第三方模块"
    if not isinstance(data, dict):
        return [f"{label}的 module.json 必须是 JSON 对象"]

    problems: list[str] = []

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        problems.append("缺少必填字段 name")
    elif not NAME_PATTERN.match(name.strip()):
        problems.append(
            f"name={name.strip()!r} 不是合法标识符"
            f"（只允许字母/数字/下划线，不能以数字开头，不允许 '.'、':'、中文、空格）"
        )

    for field in ("display_name", "version"):
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"缺少必填字段 {field}")

    api_version = data.get("api_version", DEFAULT_API_VERSION)
    if isinstance(api_version, bool) or not isinstance(api_version, int):
        problems.append(f"api_version 必须是整数，当前为 {api_version!r}")
    elif api_version not in SUPPORTED_API_VERSIONS:
        supported = ", ".join(str(v) for v in sorted(SUPPORTED_API_VERSIONS))
        problems.append(f"api_version={api_version} 不受支持（当前框架支持: {supported}）")

    entry = data.get("entry")
    if entry is not None and (not isinstance(entry, str) or not entry.strip()):
        problems.append("entry 必须是非空字符串（缺省 main.py）")

    requires = data.get("requires")
    if requires is not None:
        if not isinstance(requires, list) or any(not isinstance(item, str) for item in requires):
            problems.append("requires 必须是字符串数组")

    requirements = data.get("requirements")
    if requirements is not None and not isinstance(requirements, str):
        problems.append("requirements 必须是字符串（依赖清单文件名）")

    return problems


def build_manifest(data: dict) -> Manifest:
    """把（已通过校验的）原始 dict 转成 Manifest；缺省字段在此补齐。"""
    requires_raw = data.get("requires") or []
    requirements = data.get("requirements")
    return Manifest(
        name=str(data["name"]).strip(),
        display_name=str(data["display_name"]).strip(),
        version=str(data["version"]).strip(),
        description=str(data.get("description") or ""),
        author=str(data.get("author") or ""),
        entry=str(data.get("entry") or DEFAULT_ENTRY).strip(),
        api_version=int(data.get("api_version", DEFAULT_API_VERSION)),
        requires=tuple(str(item) for item in requires_raw),
        requirements=str(requirements) if isinstance(requirements, str) and requirements.strip() else None,
        raw=dict(data),
    )


def load_manifest(module_dir: Path) -> Manifest:
    """读取并校验 `<module_dir>/module.json`；任何问题都抛 ManifestError（带 hint）。"""
    module_dir = Path(module_dir)
    path = module_dir / MANIFEST_FILENAME
    if not path.is_file():
        raise ManifestError(
            f"模块目录 {module_dir.name!r} 缺少 {MANIFEST_FILENAME}",
            hint="第三方插件必须提供 module.json（name/display_name/version 必填，参见 docs/MODULE_DEV.md）",
        )
    try:
        raw_bytes = path.read_bytes()
    except OSError as e:
        raise ManifestError(f"无法读取 {path}: {e}", hint="检查文件权限/是否被占用") from e
    if len(raw_bytes) > MAX_MANIFEST_BYTES:
        raise ManifestError(
            f"{MANIFEST_FILENAME} 过大（{len(raw_bytes)} 字节 > {MAX_MANIFEST_BYTES}）",
            hint="module.json 只放元数据，不要放代码或长文本",
        )
    try:
        # utf-8-sig：容忍 Windows 编辑器加的 BOM（BOM 会导致 json.loads 直接失败）
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise ManifestError(
            f"{MANIFEST_FILENAME} 不是 UTF-8 编码",
            hint="用编辑器把 module.json 另存为 UTF-8（无 BOM）后重试",
        ) from e
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ManifestError(
            f"{MANIFEST_FILENAME} 不是合法 JSON：{e.msg}（第 {e.lineno} 行第 {e.colno} 列）",
            hint="检查是否多了逗号、注释或中文引号",
        ) from e

    problems = validate_manifest(data, source="installed")
    if problems:
        raise ManifestError(
            f"{MANIFEST_FILENAME} 校验失败：" + "；".join(problems),
            hint="按 docs/MODULE_DEV.md 的 module.json v2 字段表修正后重新打包",
        )
    return build_manifest(data)
