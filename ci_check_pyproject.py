"""CI 依赖元数据校验：保证 requirements.txt 与 pyproject.toml 不再漂移。

背景（真实故障）：`requirements.txt` 曾与 `pyproject.toml` 平行手工维护，导致两边漂移，
有人把 starlette 弃用文案里的 "httpx2" 抄进了 `requirements.txt`——而该包在 PyPI 上并不存在，
使首次安装（scripts/requirements.bat / .sh）必然失败。本脚本在 CI 中前置拦截这类回归。

校验项：
1. requirements.txt 必须是且只能是指向 pyproject.toml 的指针（有效行恰为 `.[dev]`）；
2. 有效依赖中不得出现已知不存在的包（httpx2）；
3. 测试期依赖（pytest / pytest-asyncio / httpx）不得出现在运行时 dependencies 中。

本地运行：venv\\Scripts\\python.exe ci_check_pyproject.py
退出码：0 = 通过，1 = 校验失败（打印 GitHub Actions ::error:: 注解）。
"""

from __future__ import annotations

import pathlib
import sys

try:  # Python 3.11+ 内置
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.10 走这里
    import tomli as tomllib  # type: ignore[no-redef]

# PyPI 上不存在、但历史上被误写入过依赖清单的包名（防回归）
_NONEXISTENT_PACKAGES = ("httpx2",)
# 只应存在于开发依赖中的包（防再次混入运行时依赖）
_DEV_ONLY_PACKAGES = ("pytest", "pytest-asyncio", "httpx")
_EXPECTED_POINTER = ".[dev]"


def read_text(path: str) -> str:
    """读取文本，容忍 Windows 编辑器可能写入的 BOM（否则首行会带 \\ufeff 导致误报）。"""
    return pathlib.Path(path).read_text(encoding="utf-8-sig")


def effective_lines(text: str) -> list[str]:
    """去掉空行与整行注释后的有效行（与 pip -r 的解析语义一致）。"""
    return [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def effective_text(path: str) -> str:
    """把文件内容里的注释剥离，只保留"有效内容"，避免注释说明被误判为依赖。"""
    out = []
    for line in read_text(path).splitlines():
        out.append("" if line.lstrip().startswith("#") else line.split("#", 1)[0])
    return "\n".join(out)


def _dep_name(spec: str) -> str:
    """从依赖声明中取出包名（去掉版本约束与 extras），如 `uvicorn[standard]>=0.23` -> `uvicorn`。

    需覆盖 PEP 508 常见版本运算符：`>=` `<=` `==` `!=` `~=` `===` `<` `>`，
    以及 extras（`[standard]`）与环境标记（`; python_version < '3.12'`）。
    注意必须先去掉 `~=`/`!=` 这类双字符运算符，否则会残留 `~`/`!` 导致包名比对失效。
    """
    name = spec.split(";", 1)[0]  # 去掉环境标记
    name = name.split("[", 1)[0]  # 去掉 extras
    # 逐字符截断到首个版本运算符
    for i, ch in enumerate(name):
        if ch in "<>=!~":
            name = name[:i]
            break
    return name.strip().lower()


def main() -> int:
    errors: list[str] = []

    # 1. requirements.txt 必须只是指针
    entries = effective_lines(read_text("requirements.txt"))
    print(f"requirements.txt 有效行: {entries}")
    if entries != [_EXPECTED_POINTER]:
        errors.append(
            f"requirements.txt 必须且只能包含一行 {_EXPECTED_POINTER!r}（依赖以 pyproject.toml 为准，避免漂移）"
        )

    # 2. 有效依赖中不得出现不存在的包
    for path in ("requirements.txt", "pyproject.toml"):
        body = effective_text(path)
        for pkg in _NONEXISTENT_PACKAGES:
            if pkg in body:
                errors.append(f"{path} 的有效依赖中出现 {pkg!r}：该包在 PyPI 上不存在，请勿写入依赖")

    # 3. 测试期依赖不得混入运行时依赖
    data = tomllib.loads(read_text("pyproject.toml"))
    project = data.get("project", {})
    runtime = project.get("dependencies", [])
    extras = project.get("optional-dependencies", {})
    if "dev" not in extras:
        errors.append("pyproject.toml 缺少 [project.optional-dependencies].dev（开发/测试依赖应声明在此）")
    dev = extras.get("dev", [])
    print(f"runtime deps: {len(runtime)}，dev deps: {len(dev)}")
    runtime_names = {_dep_name(x) for x in runtime}
    for name in _DEV_ONLY_PACKAGES:
        if name in runtime_names:
            errors.append(f"{name} 是测试期依赖，应放在 [project.optional-dependencies].dev")

    if errors:
        for e in errors:
            print(f"::error::{e}")
        return 1
    print("OK: 依赖元数据一致（requirements.txt 为纯指针，无不存在包，测试依赖未混入运行时）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
