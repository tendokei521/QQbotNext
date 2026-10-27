"""随包模块的第三方依赖检查（框架**不自动 pip 安装**，只做预检拦截）。

背景：模块的 ``module.json`` 可以用 ``requirements`` 声明一个依赖清单文件（通常
``requirements.txt``）；框架的安装器只提示、不安装。预设把模块代码带到新机器后，
如果目标环境缺这些包，模块会在加载时失败——所以要在**导入前**就点名拦住。

这里只做"读环境 + 比对包名"，不做任何安装动作（执行面保持一条）。
"""

from __future__ import annotations

import re

#: PEP 503 归一化：大小写不敏感，``-``/``_``/``.`` 等价
_NORMALIZE_RE = re.compile(r"[-_.]+")
#: 从一行 requirement 里取包名（去掉 extras / 版本约束 / 环境标记 / 注释）
_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def normalize_package(name: str) -> str:
    return _NORMALIZE_RE.sub("-", str(name or "").strip()).lower()


def parse_requirements(text: str) -> list[str]:
    """把 requirements 文件内容解析成"包名列表"（保留原始行用于提示）。

    只识别普通 requirement 行；``-r/-e/--index-url`` 等选项行与注释跳过。
    """
    packages: list[str] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        match = _NAME_RE.match(line)
        if not match:
            continue
        packages.append(line)
    return packages


def installed_packages() -> set[str]:
    """当前环境已安装的发行包名（归一化后）；探测失败按空集处理。"""
    names: set[str] = set()
    try:
        from importlib import metadata

        for dist in metadata.distributions():
            try:
                name = dist.metadata["Name"]
            except Exception:  # noqa: BLE001 —— 坏元数据跳过，不影响其它包
                continue
            if name:
                names.add(normalize_package(name))
    except Exception:  # noqa: BLE001 —— 环境不可探测时不要拖垮预检
        return set()
    return names


def missing_requirements(
    requirement_specs: list[str],
    installed: set[str] | None = None,
) -> list[str]:
    """返回 ``requirement_specs`` 里目标环境缺少的原始 requirement 行。"""
    known = installed if installed is not None else installed_packages()
    missing: list[str] = []
    for spec in requirement_specs or []:
        match = _NAME_RE.match(str(spec))
        if not match:
            continue
        if normalize_package(match.group(1)) not in known:
            missing.append(str(spec).strip())
    return missing
