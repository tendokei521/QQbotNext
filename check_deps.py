"""启动前依赖自检：比对 ``pyproject.toml`` 与实际已装库，缺什么装什么。

为什么判定逻辑只写这一份 Python（而不是 bat / sh 各写一套）：

- PEP 508 约束解析（``uvicorn[standard]>=0.23``）、版本比对、``requires-python``
  门槛，用 shell 写必然两边各维护一份，而 ``requirements.txt`` 的历史注释已经吃过
  一次"bat 与 sh 清单漂移"的亏；
- ``start.py`` 只做编排：建 venv → 升 pip/setuptools → 调本脚本 → 启动 ``main.py``，
  依赖判定一律留在这里，bat / sh 不再各写一套。

用法::

    python check_deps.py                 # 检查 + 缺失即装（默认）
    python check_deps.py --check         # 只查不装，有缺失时退出码 1
    python check_deps.py --dev           # 连 [dev] 组一起（pytest / ruff ...）
    python check_deps.py --vector        # 连 [vector] 组一起（sqlite-vec）
    python check_deps.py --json          # 机器可读报告（运维 / diag 脚本消费）

退出码：``0`` 齐全、``1`` 仍有缺失或不符（含"本机连 packaging 都找不到、无从判定"）、
``2`` 找不到 pyproject.toml、``3`` Python 版本不满足 ``requires-python``
（这种情况不尝试装包，装了也跑不起来）。
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any


def _load_packaging() -> tuple[Any, ...]:
    """找一份能用的 ``packaging``：返回 ``(六个符号..., 来源模块名)``。

    为什么不能裸 ``from packaging... import ...``：**本脚本的职责就是"把缺的包装上"，
    它自己不能被"缺包"打死**。全新 venv（Python 3.10 + pip 21.2.3 + setuptools
    57.4.0）的 site-packages 里没有独立 ``packaging``，裸导入会让自检直接崩在
    ImportError 上，永远走不到装包那一步——一键启动报的
    ``No module named 'packaging'``、紧接着 ``No module named 'pydantic'``
    就是这么来的（自检没跑成 → 依赖没装 → 真正跑 main.py 时才炸）。

    查找顺序（venv 里 pip / setuptools 必然在，所以几乎总能命中后两项）：
    独立安装的 ``packaging`` → ``pip._vendor.packaging`` → ``setuptools._vendor.packaging``。
    """
    errors: list[str] = []
    for name in ("packaging", "pip._vendor.packaging", "setuptools._vendor.packaging"):
        try:
            req_mod = importlib.import_module(f"{name}.requirements")
            spec_mod = importlib.import_module(f"{name}.specifiers")
            ver_mod = importlib.import_module(f"{name}.version")
        except (ImportError, AttributeError) as e:  # 老版本 vendored 副本可能缺符号
            errors.append(f"{name}: {e}")
            continue
        return (
            req_mod.InvalidRequirement,
            req_mod.Requirement,
            spec_mod.InvalidSpecifier,
            spec_mod.SpecifierSet,
            ver_mod.InvalidVersion,
            ver_mod.Version,
            name,
        )
    raise ImportError("no usable packaging library (" + "; ".join(errors) + ")")


try:
    (
        InvalidRequirement,
        Requirement,
        InvalidSpecifier,
        SpecifierSet,
        InvalidVersion,
        Version,
        PACKAGING_SOURCE,
    ) = _load_packaging()
    PACKAGING_ERROR: str | None = None
except ImportError as _exc:  # pragma: no cover —— venv 里有 pip 就命中不到
    InvalidRequirement = Requirement = InvalidSpecifier = SpecifierSet = None  # type: ignore[assignment]
    InvalidVersion = Version = None  # type: ignore[assignment]
    PACKAGING_SOURCE = ""
    PACKAGING_ERROR = str(_exc)

def _load_toml() -> Any:
    """找一份能用的 TOML 解析器；找不到返回 None（交给正则兜底）。

    加 ``pip._vendor.tomli`` 是为了 Python 3.10 的全新 venv：那里既没有内置
    ``tomllib``（3.11+ 才有），也没有人装过 ``tomli``，但 pip 自己必然带着一份
    vendored tomli。少了这一项，3.10 首启就只能走正则兜底（那个兜底有已知缺陷，
    见 ``_fallback_pyproject``）。
    """
    for name in ("tomllib", "tomli", "pip._vendor.tomli"):
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    return None


_toml = _load_toml()

#: 仓库根目录（本文件就在根目录，与 ``pyproject.toml`` 同级）
DEFAULT_PYPROJECT = Path(__file__).resolve().parent / "pyproject.toml"

#: 没有 TOML 解析器时的兜底：只抠依赖数组与 requires-python。
#: 数组体用"括号配对扫描"取，不能靠正则一把梭（原因见 ``_extract_array``）。
_REQUIRES_PYTHON_RE = re.compile(r"""^[ \t]*requires-python[ \t]*=[ \t]*["']([^"']+)["']""", re.M)
_QUOTED_RE = re.compile(r"""["']([^"']+)["']""")


# ==================== pyproject 读取 ====================


def _extract_array(text: str, key: str) -> str:
    """抠出 ``key = [ ... ]`` 的数组体；引号感知 + 方括号配对。

    为什么不用一条正则：依赖里带 extras 的 ``uvicorn[standard]>=0.23`` 自身含
    ``]``，非贪婪的 ``\\[(.*?)\\]`` 会在第一条 extras 处就"收尾"，把整个清单截成
    一条（实测 Python 3.10 全新 venv 里只剩 ``fastapi>=0.115`` → pydantic 等 11 条
    全漏装，启动依旧 ModuleNotFoundError）。改用最小扫描：跳过引号内字符，
    数方括号深度，深度归零才算数组结束。
    """
    start = re.search(rf"^[ \t]*{re.escape(key)}[ \t]*=[ \t]*\[", text, re.M)
    if not start:
        return ""
    depth = 1
    quote = ""
    body_start = start.end()
    for index in range(body_start, len(text)):
        ch = text[index]
        if quote:
            if ch == quote:
                quote = ""
            continue
        if ch in ("'", '"'):
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[body_start:index]
    return ""


def _fallback_pyproject(text: str) -> dict[str, Any]:
    """没有 TOML 解析器时的极简兜底（够读运行时依赖即可，别指望通用）。"""
    deps = _QUOTED_RE.findall(_extract_array(text, "dependencies"))
    requires = _REQUIRES_PYTHON_RE.search(text)
    project: dict[str, Any] = {"dependencies": deps}
    if requires:
        project["requires-python"] = requires.group(1)
    return {"project": project}


def load_pyproject(path: Path | str = DEFAULT_PYPROJECT) -> dict[str, Any]:
    """读 pyproject.toml（缺解析器时用正则兜底）。读不到就抛 ``FileNotFoundError``。

    用 ``utf-8-sig``：Windows 编辑器存带 BOM 的文件时，tomllib 会直接
    报 ``Invalid statement (at line 1, column 1)``。
    """
    text = Path(path).read_text(encoding="utf-8-sig")
    if _toml is not None:
        return _toml.loads(text)
    return _fallback_pyproject(text)


def required_specs(pyproject: dict[str, Any], groups: Sequence[str] = ()) -> list[str]:
    """取运行时依赖 + 指定可选组，返回 PEP 508 字符串列表（保持文件里的顺序）。"""
    project = pyproject.get("project") or {}
    specs = [str(item) for item in (project.get("dependencies") or [])]
    optional = project.get("optional-dependencies") or {}
    for group in groups:
        specs.extend(str(item) for item in (optional.get(group) or []))
    return specs


def requires_python(pyproject: dict[str, Any]) -> str:
    """取 ``requires-python``（没有就空串 = 不设门槛）。"""
    return str((pyproject.get("project") or {}).get("requires-python") or "")


# ==================== 检查 ====================


@dataclass(frozen=True)
class Problem:
    """一条"不满足"的记录。"""

    kind: str  # missing / mismatch / invalid
    name: str
    spec: str
    installed: str | None = None
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "spec": self.spec,
            "installed": self.installed,
            "reason": self.reason,
        }

    def describe(self) -> str:
        if self.kind == "missing":
            return f"missing (required: {self.spec})"
        if self.kind == "mismatch":
            return f"version mismatch (installed {self.installed}, required: {self.spec})"
        return f"invalid requirement declaration: {self.reason}"


def installed_version(name: str) -> str | None:
    """已装版本；没装返回 None（dist 名不区分大小写与 ``-``/``_``，交 metadata 处理）。"""
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def check_python(requires: str, version: str | None = None) -> str | None:
    """``requires-python`` 满足返回 None，否则返回一句人话原因。"""
    if not requires:
        return None
    if SpecifierSet is None:  # pragma: no cover —— 无 packaging 时无从判定，不拦
        return None
    try:
        spec = SpecifierSet(str(requires))
    except InvalidSpecifier:
        return None  # 声明本身非法就不拦，交给装包时报错
    current = version or ".".join(str(part) for part in sys.version_info[:3])
    if spec.contains(current, prereleases=True):
        return None
    return f"interpreter Python {current} does not satisfy requires-python {requires}"


def check_requirements(
    specs: Sequence[str],
    version_of: Callable[[str], str | None] = installed_version,
) -> list[Problem]:
    """逐条比对：缺包 → missing，版本不符 → mismatch，声明非法 → invalid。

    ``Requirement`` 为 None（连 pip/setuptools 的 vendored 副本都没有，理论不可达）
    时不做判定、直接当作"无条件可装"返回空列表：宁可让 pip 去撞真实错误，也不要
    留下一个静默的假"依赖齐全"。
    """
    if Requirement is None:  # pragma: no cover
        return []
    problems: list[Problem] = []
    for raw in specs:
        try:
            req = Requirement(str(raw))
        except InvalidRequirement as e:
            problems.append(Problem("invalid", str(raw), str(raw), reason=str(e)))
            continue
        found = version_of(req.name)
        if found is None:
            problems.append(Problem("missing", req.name, str(req)))
            continue
        if not req.specifier:
            continue
        try:
            actual = Version(found)
        except InvalidVersion:
            continue  # 装了个非规范版本号（本地开发态），不纠缠
        if not req.specifier.contains(actual, prereleases=True):
            problems.append(Problem("mismatch", req.name, str(req), installed=found, reason=str(req.specifier)))
    return problems


# ==================== 安装 ====================


def install_specs(
    specs: Sequence[str],
    *,
    python: str | None = None,
    timeout: float = 900,
    runner: Callable[..., Any] = subprocess.run,
) -> tuple[bool, str]:
    """调 pip 装一批 spec。返回 ``(是否成功, 说明)``；不抛异常。"""
    if not specs:
        return True, "nothing to install"
    cmd = [python or sys.executable, "-m", "pip", "install", *specs]
    print(f"[deps] installing {len(specs)} package(s): pip install {' '.join(specs)}", flush=True)
    try:
        proc = runner(cmd, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"failed to invoke pip: {e}"
    code = getattr(proc, "returncode", 1)
    return code == 0, f"pip exited with code {code}"


# ==================== CLI ====================


def _report(problems: Sequence[Problem], *, quiet: bool, checked: int = 0) -> None:
    if not problems:
        if not quiet:
            print(f"[deps] ok: {checked} requirement(s) satisfied", flush=True)
        return
    print(f"[deps] {len(problems)} requirement(s) not satisfied:", flush=True)
    for problem in problems:
        print(f"  - {problem.name}: {problem.describe()}", flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pre-flight dependency check: compare pyproject.toml with what is installed, install what is missing.",
    )
    parser.add_argument("--check", action="store_true", help="report only, never install (exit 1 when incomplete)")
    parser.add_argument("--dev", action="store_true", help="also check the [dev] optional group")
    parser.add_argument("--vector", action="store_true", help="also check the [vector] optional group")
    parser.add_argument("--group", action="append", default=[], help="extra optional group name (repeatable)")
    parser.add_argument("--pyproject", default=str(DEFAULT_PYPROJECT), help="path to pyproject.toml")
    parser.add_argument("--python", default=None, help="interpreter used to run pip (default: current)")
    parser.add_argument("--timeout", type=float, default=900, help="pip install timeout in seconds")
    parser.add_argument("--json", action="store_true", help="emit a machine-readable JSON report")
    parser.add_argument("--quiet", action="store_true", help="stay silent unless something is wrong")
    args = parser.parse_args(argv)

    if PACKAGING_ERROR is not None:  # pragma: no cover —— 只在"连 pip 都没有"的畸形环境
        print(f"[deps] cannot verify dependencies: {PACKAGING_ERROR}", file=sys.stderr, flush=True)
        print("[deps] the venv looks broken; recreate it: python -m venv venv", file=sys.stderr, flush=True)
        return 1

    try:
        pyproject = load_pyproject(args.pyproject)
    except (OSError, ValueError) as e:
        print(f"[deps] cannot read {args.pyproject}: {e}", file=sys.stderr, flush=True)
        return 2

    requires = requires_python(pyproject)
    version_problem = check_python(requires)
    if version_problem:
        print(f"[deps] {version_problem}", file=sys.stderr, flush=True)
        print("[deps] install a matching Python and try again", file=sys.stderr, flush=True)
        return 3

    groups = [g for g in args.group if g]
    if args.dev:
        groups.append("dev")
    if args.vector:
        groups.append("vector")
    specs = required_specs(pyproject, groups=groups)

    problems = check_requirements(specs)
    installed_now: list[str] = []
    if problems and not args.check:
        todo = [p.spec for p in problems if p.kind != "invalid"]
        ok, message = install_specs(todo, python=args.python, timeout=args.timeout)
        if not ok:
            print(f"[deps] {message}", file=sys.stderr, flush=True)
        problems = check_requirements(specs)
        installed_now = [p.spec for p in problems if p.kind != "invalid"]
    if args.json:
        print(
            json.dumps(
                {
                    "ok": not problems,
                    "checked": len(specs),
                    "groups": groups,
                    "python": ".".join(str(part) for part in sys.version_info[:3]),
                    "requires_python": requires,
                    "problems": [p.as_dict() for p in problems],
                    "still_failing": installed_now,
                },
                ensure_ascii=False,
                indent=2,
            ),
            flush=True,
        )
    else:
        _report(problems, quiet=args.quiet, checked=len(specs))

    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
