"""依赖元数据校验脚本（ci_check_pyproject.py）的回归测试。

背景：requirements.txt 曾与 pyproject.toml 平行维护而漂移，误入 PyPI 上不存在的
"httpx2"，导致首次安装必然失败。本测试保证该校验既拦得住回归，也不误伤正常内容。
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "ci_check_pyproject.py"


def _load():
    """按路径加载 ci_check_pyproject.py（仓库根目录下的独立脚本，非包内模块）。"""
    spec = importlib.util.spec_from_file_location("ci_check_pyproject", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ci_check_pyproject"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def checker():
    return _load()


def _write(tmp_path: pathlib.Path, req: str, proj: str) -> None:
    (tmp_path / "requirements.txt").write_text(req, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(proj, encoding="utf-8")


_GOOD_POINTER = ".[dev]\n"
_PLAIN_PROJECT = (
    "[project]\ndependencies = []\n\n[project.optional-dependencies]\ndev = []\n"
)


def _project(deps: list[str], dev: list[str] | None = None) -> str:
    """构造一份结构完整（含 dev extra）的 pyproject 片段，避免因缺段而误判。"""
    dep_lines = "".join(f'  "{d}",\n' for d in deps)
    dev_lines = "".join(f'  "{d}",\n' for d in (dev or []))
    return (
        f"[project]\ndependencies = [\n{dep_lines}]\n\n"
        f"[project.optional-dependencies]\ndev = [\n{dev_lines}]\n"
    )


def test_repo_itself_passes(checker, monkeypatch):
    """真实仓库必须通过（防止校验逻辑与仓库现状脱节）。"""
    root = _SCRIPT.parent
    monkeypatch.chdir(root)
    assert checker.main() == 0


def test_rejects_parallel_dependency_list(checker, tmp_path, monkeypatch):
    """requirements.txt 若又变成平行清单（含 httpx2），必须拦截——即原始故障场景。"""
    _write(tmp_path, "fastapi>=0.115\nhttpx2\n", _PLAIN_PROJECT)
    monkeypatch.chdir(tmp_path)
    assert checker.main() == 1


def test_rejects_nonexistent_package_in_pyproject(checker, tmp_path, monkeypatch):
    _write(tmp_path, _GOOD_POINTER, _project(["httpx2"]))
    monkeypatch.chdir(tmp_path)
    assert checker.main() == 1


def test_ignores_nonexistent_package_in_comments(checker, tmp_path, monkeypatch):
    """注释里提到 httpx2 是在说明历史，不应当作依赖拦截（否则正常仓库会误报）。"""
    _write(
        tmp_path,
        "# 历史：曾误加 httpx2（PyPI 不存在）\n.[dev]\n",
        "# 说明：不要写 httpx2\n" + _PLAIN_PROJECT,
    )
    monkeypatch.chdir(tmp_path)
    assert checker.main() == 0


def test_tolerates_bom(checker, tmp_path, monkeypatch):
    """Windows 编辑器可能写入 BOM，首行不应因此被判为非法。"""
    (tmp_path / "requirements.txt").write_bytes("# note: httpx2\n.[dev]\n".encode("utf-8-sig"))
    (tmp_path / "pyproject.toml").write_text(_PLAIN_PROJECT, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert checker.main() == 0


def test_rejects_dev_dependency_in_runtime(checker, tmp_path, monkeypatch):
    _write(tmp_path, _GOOD_POINTER, _project(["pytest>=7.4"], ["pytest>=7.4"]))
    monkeypatch.chdir(tmp_path)
    assert checker.main() == 1


def test_missing_dev_extra_is_reported(checker, tmp_path, monkeypatch):
    """结构缺失（无 dev extra）应给出明确错误，而非抛 KeyError。"""
    _write(tmp_path, _GOOD_POINTER, "[project]\ndependencies = []\n")
    monkeypatch.chdir(tmp_path)
    assert checker.main() == 1


def test_dep_name_handles_extras_and_versions(checker):
    """`uvicorn[standard]>=0.23` 之类的声明要能正确取出包名，避免误判。"""
    assert checker._dep_name("uvicorn[standard]>=0.23") == "uvicorn"
    assert checker._dep_name("curl_cffi>=0.15") == "curl_cffi"
    assert checker._dep_name("pytest-asyncio~=0.21") == "pytest-asyncio"
    assert checker._dep_name("httpx-sse; python_version<'3.12'") == "httpx-sse"
    assert checker._dep_name("tomli!=2.0.0") == "tomli"


def test_extras_package_is_not_false_positive(checker, tmp_path, monkeypatch):
    """含 extras 的运行时依赖不应被测试期依赖规则误伤。"""
    _write(tmp_path, _GOOD_POINTER, _project(["uvicorn[standard]>=0.23"], ["pytest>=7.4"]))
    monkeypatch.chdir(tmp_path)
    assert checker.main() == 0
