"""模块依赖：`requires` 声明自检 + `module.json` 的 pip 依赖清单解析/安装支持。

两部分：

1. **框架能力自检**（本文件原有职责）：模块用 ``ctx.services.llm`` / 某 feature 时声明
   ``requires``，加载时校验，缺失只警告不阻断（模块可自行降级）。
2. **pip 依赖清单**：``module.json`` 可用 ``requirements`` 声明一个依赖清单文件
   （通常 ``requirements.txt``）。安装器据此算出"当前环境缺哪些包"（预检），
   用户显式确认后再由 ``installer.py`` 用 pip 子进程安装。

pip 安装的安全边界在这里收口（清单来自第三方 zip，必须当成不可信输入）：

- 只接受「包名 + 可选 extras + 版本约束」的行，拒绝 ``-e`` / ``-r`` / URL / 环境标记
  ——这些写法能把 pip 变成任意指令/任意源入口；
- 依赖清单文件路径必须落在插件目录内（防 ``../../.env`` 这类任意文件读取）；
- 镜像地址只接受 ``http(s)://`` 与受限字符集（防把 URL 拼进 argv 变成其它选项）。

名字解析口径（与 ``collect_capabilities`` 的清单一致）：

1. ``ctx.services.<name>`` 非空 → 满足（服务，如 ``llm`` / ``role_presets``）；
2. 否则 ``FeatureRegistry`` 注册了同名 feature → 满足（能力，如 ``assembly``）；
3. 都没有 → 记入缺失（调用方只警告，模块可据此在自己的逻辑里降级）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.core.logger import logger
from app.plugin.manifest import Manifest

#: pip 安装超时（秒）与回传输出上限（字符：够定位问题，又不至于撑爆 HTTP 响应）
PIP_TIMEOUT = 180.0
PIP_OUTPUT_LIMIT = 8000
#: 允许从插件依赖清单里安装的行：包名 + 可选 extras + 可选版本约束。
_REQUIREMENT_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*"                          # 包名
    r"(\[[A-Za-z0-9._,-]+\])?"                              # extras
    r"((===|==|>=|<=|~=|!=|>|<)[A-Za-z0-9.*+!_-]+"
    r"(,(===|==|>=|<=|~=|!=|>|<)[A-Za-z0-9.*+!_-]+)*)?$"     # 版本约束
)
#: pip 镜像地址白名单：只接受 http(s)，且字符集受限（防 URL 被当作命令行选项）
_INDEX_URL_RE = re.compile(r"^https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+$")
#: PEP 503 包名归一化：大小写不敏感，``-``/``_``/``.`` 等价
_NORMALIZE_RE = re.compile(r"[-_.]+")


def declared_requirements(module: Any) -> list[str]:
    """模块声明的依赖名（去空白、去重保序）。"""
    raw = getattr(module, "requires", ()) or ()
    names: list[str] = []
    for item in raw:
        name = str(item or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def missing_requirements(module: Any, *, services: Any = None, features: Any = None) -> list[str]:
    """返回框架**没有提供**的依赖名（空列表 = 全部满足，或模块没声明依赖）。"""
    names = declared_requirements(module)
    if not names:
        return []

    if services is None:
        services = getattr(getattr(module, "ctx", None), "services", None)
    if features is None and services is not None:
        features = getattr(services, "features", None)

    missing: list[str] = []
    for name in names:
        if getattr(services, name, None) is not None:
            continue
        get_feature = getattr(features, "get", None)
        if callable(get_feature) and get_feature(name) is not None:
            continue
        missing.append(name)
    return missing


# ==================== pip 依赖清单（安装器用） ====================


def normalize_package(name: str) -> str:
    """PEP 503 归一化包名（大小写、``-``/``_``/``.`` 差异不影响判定）。"""
    return _NORMALIZE_RE.sub("-", str(name or "").strip()).lower()


def is_safe_index_url(url: str) -> bool:
    """镜像地址白名单校验（只允许 http(s) + 受限字符集）。"""
    return bool(_INDEX_URL_RE.match(str(url or "").strip()))


def requirements_file_for(plugin_dir: Path, manifest: Manifest) -> Path | None:
    """解析清单声明的依赖清单文件；越界（绝对路径/``../``）或不存在返回 None。

    依赖清单来自第三方 zip，路径必须限制在插件目录内——否则一个 ``../../.env``
    就能让"读依赖"变成任意文件读取。
    """
    declared = (manifest.requirements or "").strip()
    if not declared:
        return None
    plugin_root = Path(plugin_dir).resolve()
    candidate = (plugin_root / declared).resolve()
    try:
        candidate.relative_to(plugin_root)
    except ValueError:
        logger.warning(
            f"[Module] {plugin_root.name} 的 requirements={declared!r} 指向插件目录之外，已拒绝"
        )
        return None
    return candidate if candidate.is_file() else None


def read_requirement_specs(path: Path) -> tuple[list[str], list[str]]:
    """读依赖清单：返回 (合法行, 非法行)。注释/空行忽略，其余必须匹配白名单正则。"""
    allowed: list[str] = []
    rejected: list[str] = []
    for raw_line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if _REQUIREMENT_RE.match(line):
            allowed.append(line)
        else:
            rejected.append(line)
    return allowed, rejected


def installed_distributions() -> set[str]:
    """当前环境已安装发行包名集合（PEP 503 归一化）；探测失败按空集处理并告警。"""
    names: set[str] = set()
    try:
        from importlib import metadata

        for dist in metadata.distributions():
            name = (dist.metadata["Name"] or "").strip()
            if name:
                names.add(normalize_package(name))
    except Exception as e:  # noqa: BLE001 —— 环境元数据损坏不该让依赖预检整体失败
        logger.warning(f"[Module] 读取已安装依赖清单失败（按空集处理）: {e}")
        return set()
    return names


def missing_requirement_specs(specs: list[str], installed: set[str] | None = None) -> list[str]:
    """返回 specs 里当前环境缺的原始行（已安装的跳过，可重复调用做安装后复核）。"""
    known = installed if installed is not None else installed_distributions()
    missing: list[str] = []
    for spec in specs:
        name = re.split(r"[\[<>=!~ ]", spec, maxsplit=1)[0]
        if normalize_package(name) not in known:
            missing.append(spec)
    return missing


def inspect_dependencies(plugin_dir: Path, manifest: Manifest) -> dict:
    """只读依赖预检：清单文件、声明行、非法行、当前缺失包。"""
    req_file = requirements_file_for(plugin_dir, manifest)
    if req_file is None:
        return {"file": None, "declared": [], "rejected": [], "missing": []}
    declared, rejected = read_requirement_specs(req_file)
    return {
        "file": req_file.name,
        "declared": declared,
        "rejected": rejected,
        "missing": missing_requirement_specs(declared),
    }


def truncate_pip_output(data: bytes | None) -> str:
    """pip 输出转文本并截断到尾部（错误通常在最后几行）。"""
    text = (data or b"").decode("utf-8", "replace").strip()
    if len(text) <= PIP_OUTPUT_LIMIT:
        return text
    return "…（输出过长，仅保留末尾）…\n" + text[-PIP_OUTPUT_LIMIT:]
