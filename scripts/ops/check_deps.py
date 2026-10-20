"""启动前依赖自检：比对 ``pyproject.toml`` 与实际已装库，缺什么装什么。

为什么判定逻辑只写这一份 Python（而不是 bat / sh 各写一套）：

- PEP 508 约束解析（``uvicorn[standard]>=0.23``）、版本比对、``requires-python``
  门槛，用 shell 写必然两边各维护一份，而 ``requirements.txt`` 的历史注释已经吃过
  一次"bat 与 sh 清单漂移"的亏；
- ``start.bat`` / ``start.sh`` 只做薄壳：找到解释器 → 调本脚本 → 按退出码提示。

用法::

    python scripts/ops/check_deps.py                 # 检查 + 缺失即装（默认）
    python scripts/ops/check_deps.py --check         # 只查不装，有缺失时退出码 1
    python scripts/ops/check_deps.py --dev           # 连 [dev] 组一起（pytest / ruff ...）
    python scripts/ops/check_deps.py --vector        # 连 [vector] 组一起（sqlite-vec）
    python scripts/ops/check_deps.py --json          # 机器可读报告（运维 / diag 脚本消费）

退出码：``0`` 齐全、``1`` 仍有缺失或不符、``2`` 找不到 pyproject.toml、
``3`` Python 版本不满足 ``requires-python``（这种情况不尝试装包，装了也跑不起来）。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

try:  # Python 3.11+
    import tomllib as _toml
except ImportError:  # pragma: no cover —— 3.10 走 tomli，两者都没有才走正则兜底
    try:
        import tomli as _toml  # type: ignore[no-redef]
    except ImportError:
        _toml = None  # type: ignore[assignment]

#: 仓库根目录（本文件在 ``scripts/ops/`` 下）
DEFAULT_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"

#: 没有 tomllib / tomli 时的兜底：只抠 ``dependencies = [...]`` 与 requires-python
_DEP_ARRAY_RE = re.compile(r"^[ \t]*dependencies[ \t]*=[ \t]*\[(.*?)\]", re.S | re.M)
_REQUIRES_PYTHON_RE = re.compile(r"""^[ \t]*requires-python[ \t]*=[ \t]*["']([^"']+)["']""", re.M)
_QUOTED_RE = re.compile(r"""["']([^"']+)["']""")


# ==================== pyproject 读取 ====================


def _fallback_pyproject(text: str) -> dict[str, Any]:
    """没有 TOML 解析器时的极简兜底（够读运行时依赖即可，别指望通用）。"""
    block = _DEP_ARRAY_RE.search(text)
    deps = _QUOTED_RE.findall(block.group(1)) if block else []
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
            return f"缺少 {self.spec}"
        if self.kind == "mismatch":
            return f"版本不符 {self.spec}（已装 {self.installed}）"
        return f"依赖声明非法 {self.spec}：{self.reason}"


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
    try:
        spec = SpecifierSet(str(requires))
    except InvalidSpecifier:
        return None  # 声明本身非法就不拦，交给装包时报错
    current = version or ".".join(str(part) for part in sys.version_info[:3])
    if spec.contains(current, prereleases=True):
        return None
    return f"当前 Python {current} 不满足 requires-python {requires}"


def check_requirements(
    specs: Sequence[str],
    version_of: Callable[[str], str | None] = installed_version,
) -> list[Problem]:
    """逐条比对：缺包 → missing，版本不符 → mismatch，声明非法 → invalid。"""
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
        return True, "无需安装"
    cmd = [python or sys.executable, "-m", "pip", "install", *specs]
    print(f"[deps] 安装缺失依赖 → {' '.join(cmd[3:])}", flush=True)
    try:
        proc = runner(cmd, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"调用 pip 失败：{e}"
    code = getattr(proc, "returncode", 1)
    return code == 0, f"pip 退出码 {code}"


# ==================== CLI ====================


def _report(problems: Sequence[Problem], *, quiet: bool) -> None:
    if not problems:
        if not quiet:
            print("[deps] 依赖齐全，可以启动。", flush=True)
        return
    print(f"[deps] 发现 {len(problems)} 项问题：", flush=True)
    for problem in problems:
        print(f"  - {problem.name}: {problem.describe()}", flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="启动前依赖自检（缺什么装什么）")
    parser.add_argument("--check", action="store_true", help="只检查、不安装（缺则退出码 1）")
    parser.add_argument("--dev", action="store_true", help="连同 [dev] 可选组一起检查")
    parser.add_argument("--vector", action="store_true", help="连同 [vector] 可选组一起检查")
    parser.add_argument("--group", action="append", default=[], help="额外指定可选组名（可重复）")
    parser.add_argument("--pyproject", default=str(DEFAULT_PYPROJECT), help="pyproject.toml 路径")
    parser.add_argument("--python", default=None, help="用哪个解释器跑 pip（缺省当前解释器）")
    parser.add_argument("--timeout", type=float, default=900, help="pip 安装超时秒数")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON 报告")
    parser.add_argument("--quiet", action="store_true", help="只在有问题时出声")
    args = parser.parse_args(argv)

    try:
        pyproject = load_pyproject(args.pyproject)
    except (OSError, ValueError) as e:
        print(f"[deps] 读不了 {args.pyproject}：{e}", file=sys.stderr, flush=True)
        return 2

    requires = requires_python(pyproject)
    version_problem = check_python(requires)
    if version_problem:
        print(f"[deps] {version_problem}；请换对应版本的 Python 再启动。", file=sys.stderr, flush=True)
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
        _report(problems, quiet=args.quiet)

    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
