"""启动前依赖自检（根目录 ``check_deps.py``）测试。

盯住六件事：

1. **读得动 pyproject**：运行时依赖与 ``requires-python`` 都抠得出来，带 BOM 的
   文件也不炸（Windows 编辑器很爱加 BOM，tomllib 见到就直接报错）；
2. **可选组**：``--dev`` / ``--vector`` 能把 ``[project.optional-dependencies]`` 并进来；
3. **四类判定**：缺包 → missing、版本不符 → mismatch、声明非法 → invalid、满足 → 不报；
4. **Python 门槛**：``requires-python`` 不满足时给一句人话原因，且不尝试装包；
5. **装包命令形状**：``install_specs`` 拼的是 ``<python> -m pip install <specs>``，
   调用失败返回 ``(False, 原因)`` 而不抛；
6. **退出码契约**：0 齐全 / 1 有缺失 / 2 无 pyproject / 3 Python 版本不行。
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest


def _repo_root() -> Path:
    """向上找含 ``pyproject.toml`` 的目录。

    不写死 ``parents[N]``：本文件最初在 ``tests/``、后被移到 ``tests/architecture/``，
    层级一变 ``parents[1]`` 就指向 ``tests/scripts/...``（实测直接报文件不存在）。
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    raise RuntimeError("找不到仓库根（pyproject.toml）")


MODULE_PATH = _repo_root() / "check_deps.py"


def _load_module():
    """从路径加载脚本模块（它不是包的一部分，不能直接 import）。

    必须先塞进 ``sys.modules``：``dataclasses`` 在装饰阶段会回头查
    ``sys.modules[cls.__module__]``，否则实测抛 AttributeError。
    """
    spec = importlib.util.spec_from_file_location("check_deps_under_test", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check_deps = _load_module()


# ==================== 读 pyproject ====================


def test_load_real_pyproject_has_runtime_deps_and_python_floor():
    data = check_deps.load_pyproject()

    specs = check_deps.required_specs(data)
    assert any(spec.startswith("pillow") for spec in specs)  # 降采样靠它，必须在运行时依赖里
    assert check_deps.requires_python(data).startswith(">=")


def test_load_pyproject_tolerates_utf8_bom(tmp_path):
    target = tmp_path / "pyproject.toml"
    target.write_text('[project]\nrequires-python = ">=3.10"\ndependencies = ["pillow>=10.0"]\n', encoding="utf-8-sig")

    data = check_deps.load_pyproject(target)

    assert check_deps.required_specs(data) == ["pillow>=10.0"]


def test_load_pyproject_missing_file_raises(tmp_path):
    with pytest.raises(OSError):
        check_deps.load_pyproject(tmp_path / "nope.toml")


def test_required_specs_merges_optional_groups():
    data = {
        "project": {
            "dependencies": ["fastapi>=0.115"],
            "optional-dependencies": {"dev": ["pytest>=7.4"], "vector": ["sqlite-vec>=0.1.6"]},
        }
    }

    assert check_deps.required_specs(data) == ["fastapi>=0.115"]
    assert check_deps.required_specs(data, groups=["dev"]) == ["fastapi>=0.115", "pytest>=7.4"]
    assert check_deps.required_specs(data, groups=["dev", "vector"])[-1] == "sqlite-vec>=0.1.6"


def test_fallback_parser_works_without_toml_library():
    """没有 tomllib / tomli 时的兜底：只要求抠出依赖清单与 Python 门槛。"""
    text = (
        '[project]\nname = "x"\nrequires-python = ">=3.10"\n'
        'dependencies = [\n    "pillow>=10.0",\n    "fastapi>=0.115",  # 注释\n]\n'
    )

    data = check_deps._fallback_pyproject(text)

    assert check_deps.required_specs(data) == ["pillow>=10.0", "fastapi>=0.115"]
    assert check_deps.requires_python(data) == ">=3.10"


# ==================== 判定 ====================


def test_check_requirements_classifies_missing_mismatch_and_ok():
    installed = {"pillow": "12.2.0"}

    problems = check_deps.check_requirements(
        ["pillow>=10.0", "ghost-pkg>=1.0", "pillow>=99.0"],
        version_of=installed.get,
    )

    kinds = [problem.kind for problem in problems]
    assert kinds == ["missing", "mismatch"]
    assert problems[0].name == "ghost-pkg"
    assert problems[1].installed == "12.2.0"
    assert "12.2.0" in problems[1].describe()


def test_check_requirements_accepts_extras_and_bare_names():
    installed = {"uvicorn": "0.30.0", "aiosqlite": "0.20.0"}

    problems = check_deps.check_requirements(["uvicorn[standard]>=0.23", "aiosqlite"], version_of=installed.get)

    assert problems == []


def test_check_requirements_flags_invalid_declaration():
    problems = check_deps.check_requirements(["not a spec >>>"], version_of=lambda _name: None)

    assert [problem.kind for problem in problems] == ["invalid"]


def test_check_requirements_ignores_non_pep440_installed_version():
    """本地开发态装了个乱版本号时不纠缠（按没装到规范版本处理以外，直接放过）。"""
    problems = check_deps.check_requirements(["pillow>=10.0"], version_of=lambda _name: "dev-build")

    assert problems == []


def test_installed_version_returns_none_for_unknown_package():
    assert check_deps.installed_version("qqbot-next-definitely-not-installed") is None


def test_installed_version_reads_real_metadata():
    assert check_deps.installed_version("pillow")  # pillow 是运行时依赖，测试环境必有


# ==================== Python 门槛 ====================


def test_check_python_passes_and_fails():
    assert check_deps.check_python(">=3.10", version="3.10.0") is None
    assert check_deps.check_python("", version="3.10.0") is None

    reason = check_deps.check_python(">=3.12", version="3.10.0")

    assert reason is not None and "3.10.0" in reason and ">=3.12" in reason


def test_check_python_ignores_unparsable_floor():
    assert check_deps.check_python("not-a-spec", version="3.10.0") is None


# ==================== 装包命令 ====================


class _Proc:
    def __init__(self, returncode):
        self.returncode = returncode


def test_install_specs_builds_pip_command():
    calls = []

    def runner(cmd, timeout=None):
        calls.append((cmd, timeout))
        return _Proc(0)

    ok, message = check_deps.install_specs(["pillow>=10.0", "fastapi>=0.115"], python="PY", timeout=30, runner=runner)

    assert ok is True and message == "pip exited with code 0"
    cmd, timeout = calls[0]
    assert cmd == ["PY", "-m", "pip", "install", "pillow>=10.0", "fastapi>=0.115"]
    assert timeout == 30


def test_install_specs_reports_failure_without_raising():
    ok, message = check_deps.install_specs(["pillow>=10.0"], runner=lambda cmd, timeout=None: _Proc(2))

    assert ok is False and "2" in message


def test_install_specs_handles_oserror():
    def runner(cmd, timeout=None):
        raise OSError("no pip")

    ok, message = check_deps.install_specs(["pillow>=10.0"], runner=runner)

    assert ok is False and "no pip" in message


def test_install_specs_noop_on_empty_list():
    ok, message = check_deps.install_specs([])

    assert ok is True and "nothing to install" in message


# ==================== CLI 退出码契约 ====================


def _write_pyproject(tmp_path, body: str) -> str:
    target = tmp_path / "pyproject.toml"
    target.write_text(body, encoding="utf-8")
    return str(target)


def test_main_check_returns_zero_when_all_satisfied(tmp_path, capsys):
    path = _write_pyproject(
        tmp_path,
        '[project]\nrequires-python = ">=3.10"\ndependencies = ["pillow>=10.0"]\n',
    )

    code = check_deps.main(["--check", "--pyproject", path])

    assert code == 0
    capsys.readouterr()


def test_main_check_returns_one_and_lists_problems(tmp_path, capsys):
    path = _write_pyproject(
        tmp_path,
        '[project]\nrequires-python = ">=3.10"\n'
        'dependencies = ["pillow>=10.0", "qqbot-next-definitely-not-installed>=1.0"]\n',
    )

    code = check_deps.main(["--check", "--pyproject", path])
    out = capsys.readouterr().out

    assert code == 1
    assert "qqbot-next-definitely-not-installed" in out


def test_main_check_json_report_is_machine_readable(tmp_path, capsys):
    path = _write_pyproject(
        tmp_path,
        '[project]\nrequires-python = ">=3.10"\n'
        'dependencies = ["pillow>=10.0", "qqbot-next-definitely-not-installed>=1.0"]\n',
    )

    code = check_deps.main(["--check", "--json", "--pyproject", path])
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    assert payload["ok"] is False
    assert payload["problems"][0]["kind"] == "missing"
    assert payload["requires_python"] == ">=3.10"


def test_main_returns_three_when_python_too_old(tmp_path, capsys):
    path = _write_pyproject(tmp_path, '[project]\nrequires-python = ">=99.0"\ndependencies = ["pillow>=10.0"]\n')

    code = check_deps.main(["--check", "--pyproject", path])

    assert code == 3
    assert "requires-python" in capsys.readouterr().err


def test_main_returns_two_when_pyproject_unreadable(tmp_path):
    code = check_deps.main(["--check", "--pyproject", str(tmp_path / "missing.toml")])

    assert code == 2


def test_main_quiet_stays_silent_when_clean(tmp_path, capsys):
    path = _write_pyproject(tmp_path, '[project]\nrequires-python = ">=3.10"\ndependencies = ["pillow>=10.0"]\n')

    assert check_deps.main(["--check", "--quiet", "--pyproject", path]) == 0
    assert capsys.readouterr().out == ""
