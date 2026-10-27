"""一键启动器（根目录 ``start.py``）测试。

盯住"一键"这件事真正会翻车的几处：

1. **平台路径**：venv 里的解释器在 Windows 是 ``Scripts/python.exe``、其它平台是
   ``bin/python3``，拼错就等于永远建好却永远用不上；
2. **判活而非判存在**：venv 目录还在、底座解释器已被卸载时，文件存在但进程起不来——
   ``interpreter_works`` 必须真跑一句 ``import sys``；
3. **自愈**：解释器没了（或 ``--recreate``）要能自己重建，不需要用户手删目录；
4. **删除护栏**：``--venv`` 手滑指向真实目录时，``remove_venv`` 必须拒绝；
5. **依赖不齐就不启动**：这是旧版 ``start.bat`` 的病根（"继续 anyway" 换来一屏
   ``ModuleNotFoundError``），退出码契约要钉死；
6. **``--check`` 只读**：不联网、不升级工具链、不启动。
"""

import importlib.util
import sys
from pathlib import Path

import pytest


def _repo_root() -> Path:
    """向上找含 ``pyproject.toml`` 的目录（不写死 ``parents[N]``）。"""
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    raise RuntimeError("找不到仓库根（pyproject.toml）")


MODULE_PATH = _repo_root() / "start.py"


def _load_module():
    """从路径加载 ``start.py``（它不是包的一部分，不能直接 import）。"""
    spec = importlib.util.spec_from_file_location("start_under_test", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


start = _load_module()


class _Proc:
    """替身：只带 ``returncode`` 与可选输出。"""

    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _write_launcher_files(tmp_path, monkeypatch, *, make_main=True):
    """铺一个"假 venv + 假入口"，并把模块常量指过去。"""
    venv = tmp_path / "venv"
    python = start.venv_python_path(venv)
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_bytes(b"")
    (venv / "pyvenv.cfg").write_text("home = nowhere\n", encoding="utf-8")

    main_script = tmp_path / "main.py"
    if make_main:
        main_script.write_text("print('hi')\n", encoding="utf-8")
    deps_script = tmp_path / "check_deps.py"
    deps_script.write_text("# stub\n", encoding="utf-8")

    monkeypatch.setattr(start, "MAIN_SCRIPT", main_script)
    monkeypatch.setattr(start, "DEPS_SCRIPT", deps_script)
    monkeypatch.setattr(start, "ROOT", tmp_path)
    # launch() 会 chdir 到仓库根；测试里屏蔽掉，免得动了 pytest 的 cwd
    monkeypatch.setattr(start.os, "chdir", lambda _path: None)
    return venv, python


def _runner(calls, *, deps_rc=0, on_venv_create=None):
    """记录调用；``check_deps.py`` 的调用返回指定退出码，其余成功。"""

    def runner(cmd, **_kwargs):
        calls.append([str(part) for part in cmd])
        if "-m" in cmd and "venv" in cmd:
            if on_venv_create:
                on_venv_create()
            return _Proc(0)
        if any(str(part).endswith("check_deps.py") for part in cmd):
            return _Proc(deps_rc)
        return _Proc(0)

    return runner


# ==================== 路径与探活 ====================


def test_venv_python_path_follows_platform(monkeypatch):
    monkeypatch.setattr(start, "IS_WINDOWS", True)
    assert start.venv_python_path(Path("venv")).as_posix() == "venv/Scripts/python.exe"

    monkeypatch.setattr(start, "IS_WINDOWS", False)
    assert start.venv_python_path(Path("venv")).as_posix() == "venv/bin/python3"


def test_interpreter_works_requires_existing_file(tmp_path):
    assert start.interpreter_works(tmp_path / "nope.exe", runner=lambda *_a, **_k: _Proc(0)) is False


def test_interpreter_works_probes_instead_of_trusting_existence(tmp_path):
    """文件在 ≠ 解释器能用：底座 Python 被卸载后正是这种"僵尸 venv"。"""
    exe = tmp_path / "python.exe"
    exe.write_bytes(b"")

    assert start.interpreter_works(exe, runner=lambda *_a, **_k: _Proc(0)) is True
    assert start.interpreter_works(exe, runner=lambda *_a, **_k: _Proc(1)) is False

    def boom(*_args, **_kwargs):
        raise OSError("cannot start")

    assert start.interpreter_works(exe, runner=boom) is False


def test_base_python_prefers_base_prefix_inside_a_venv(monkeypatch):
    """在某个 venv 里跑时，要拿底座解释器去建下一个 venv（venv 建 venv 会被拒）。"""
    monkeypatch.setattr(start.sys, "prefix", "/fake/venv")
    monkeypatch.setattr(start.sys, "base_prefix", "/fake/base")
    monkeypatch.setattr(start.Path, "exists", lambda _self: True, raising=False)
    monkeypatch.setattr(start.sys, "executable", "/fake/venv/bin/python")

    assert start.base_python().replace("\\", "/").startswith("/fake/base")


# ==================== 删除护栏 ====================


def test_remove_venv_removes_only_real_venv(tmp_path):
    target = tmp_path / "venv"
    target.mkdir()
    (target / "pyvenv.cfg").write_text("", encoding="utf-8")

    ok, _detail = start.remove_venv(target)

    assert ok is True and not target.exists()


def test_remove_venv_refuses_directory_that_is_not_a_venv(tmp_path):
    target = tmp_path / "some-project"
    target.mkdir()
    (target / "keep.txt").write_text("precious", encoding="utf-8")

    ok, detail = start.remove_venv(target)

    assert ok is False and target.exists() and "拒绝删除" in detail


def test_create_venv_reports_last_error_line(tmp_path):
    def runner(_cmd, **_kwargs):
        return _Proc(1, stderr="Error: [Errno 2] No such file\n", stdout="")

    ok, detail = start.create_venv(tmp_path / "venv", runner=runner)

    assert ok is False and "No such file" in detail


# ==================== 命令拼装 ====================


def test_dependency_cmd_passes_flags_through():
    cmd = start.dependency_cmd(Path("py"), check_only=True, dev=True, vector=True, groups=["extra"])

    assert cmd[0] == "py"
    assert Path(cmd[1]).name == "check_deps.py"
    assert cmd[2] == "--quiet"
    assert "--check" in cmd and "--dev" in cmd and "--vector" in cmd
    assert cmd[cmd.index("--group") + 1] == "extra"


def test_launch_cmd_targets_main_py():
    cmd = start.launch_cmd(Path("py"), ["--debug", "x"])

    assert cmd[0] == "py"
    assert Path(cmd[1]).name == "main.py"
    assert cmd[2:] == ["--debug", "x"]


def test_launch_enables_webui_auto_open_by_default(monkeypatch):
    """本地启动脚本默认打开浏览器（main.py 读 WEBUI_AUTO_OPEN）。"""
    monkeypatch.delenv("WEBUI_AUTO_OPEN", raising=False)
    start.launch(Path("py"), execv=lambda prog, args: None)

    assert start.os.environ["WEBUI_AUTO_OPEN"] == "1"


def test_launch_no_open_turns_auto_open_off(monkeypatch):
    monkeypatch.setenv("WEBUI_AUTO_OPEN", "1")
    start.launch(Path("py"), execv=lambda prog, args: None, auto_open=False)

    assert start.os.environ["WEBUI_AUTO_OPEN"] == "0"


def test_no_open_flag_reaches_launch(tmp_path, monkeypatch):
    """`--no-open` 必须真的传到 launch，否则用户关不掉自动打开。"""
    venv, _python = _write_launcher_files(tmp_path, monkeypatch)
    monkeypatch.delenv("WEBUI_AUTO_OPEN", raising=False)
    calls, execd = [], []

    code = start.main(
        ["--venv", str(venv), "--no-open"],
        runner=_runner(calls),
        execv=lambda prog, args: execd.append((prog, args)),
    )

    assert code == start.EXIT_OK
    assert start.os.environ["WEBUI_AUTO_OPEN"] == "0"


# ==================== 主流程 ====================


def test_main_launches_with_venv_python_when_dependencies_ok(tmp_path, monkeypatch):
    venv, python = _write_launcher_files(tmp_path, monkeypatch)
    calls, execd = [], []
    argv = ["--venv", str(venv)]

    code = start.main(argv, runner=_runner(calls), execv=lambda prog, args: execd.append((prog, list(args))))

    assert code == start.EXIT_OK
    assert execd, "依赖齐全时必须真的把进程交给 main.py"
    assert execd[0][0] == str(python)
    assert Path(execd[0][1][1]).name == "main.py"
    # 工具链升级必须发生在查依赖之前，否则裸 venv 装不动 pyproject 后端
    upgrade_at = next(i for i, c in enumerate(calls) if "--upgrade" in c)
    deps_at = next(i for i, c in enumerate(calls) if any(p.endswith("check_deps.py") for p in c))
    assert upgrade_at < deps_at


def test_main_does_not_launch_when_dependencies_incomplete(tmp_path, monkeypatch):
    """核心回归：旧版在这里"继续 anyway"，用户拿到的是一屏 ModuleNotFoundError。"""
    venv, _python = _write_launcher_files(tmp_path, monkeypatch)
    calls, execd = [], []
    argv = ["--venv", str(venv)]

    code = start.main(argv, runner=_runner(calls, deps_rc=1), execv=lambda prog, args: execd.append((prog, args)))

    assert code == start.EXIT_DEPS
    assert execd == []


def test_main_force_launch_overrides_incomplete_dependencies(tmp_path, monkeypatch):
    venv, _python = _write_launcher_files(tmp_path, monkeypatch)
    execd = []
    argv = ["--venv", str(venv), "--force-launch"]

    code = start.main(argv, runner=_runner([], deps_rc=1), execv=lambda prog, args: execd.append((prog, args)))

    assert code == start.EXIT_OK
    assert len(execd) == 1


def test_main_reports_python_version_mismatch_distinctly(tmp_path, monkeypatch, capsys):
    """check_deps 的退出码 3 = Python 版本不符，不能提示"查网络/配镜像"。"""
    venv, _python = _write_launcher_files(tmp_path, monkeypatch)
    execd = []

    code = start.main(
        ["--venv", str(venv)],
        runner=_runner([], deps_rc=3),
        execv=lambda prog, args: execd.append((prog, args)),
    )

    assert code == start.EXIT_PYTHON
    assert execd == [], "版本不符时不该继续启动"
    out = capsys.readouterr().out
    assert "Python 不满足运行要求" in out
    assert "没联网" not in out and "没配镜像" not in out


def test_check_propagates_python_version_mismatch(tmp_path, monkeypatch):
    """--check 也必须回传真实原因（4），不能折成 2。"""
    venv, _python = _write_launcher_files(tmp_path, monkeypatch)

    code = start.main(["--venv", str(venv), "--check"], runner=_runner([], deps_rc=3), execv=lambda *_: None)

    assert code == start.EXIT_PYTHON


def test_exit_codes_are_distinct():
    """五个退出码语义不同，不许合并（合并会把用户引向错误修复动作）。"""
    codes = {start.EXIT_OK, start.EXIT_VENV, start.EXIT_DEPS, start.EXIT_ENTRY, start.EXIT_PYTHON}
    assert len(codes) == 5


def test_main_check_is_read_only(tmp_path, monkeypatch):
    venv, _python = _write_launcher_files(tmp_path, monkeypatch)
    calls, execd = [], []
    argv = ["--venv", str(venv), "--check"]

    code = start.main(argv, runner=_runner(calls), execv=lambda prog, args: execd.append((prog, args)))

    assert code == start.EXIT_OK
    assert execd == []
    assert not any("--upgrade" in cmd for cmd in calls), "--check 不得联网升级工具链"
    assert any("--check" in cmd for cmd in calls)


def test_main_no_launch_prepares_without_exec(tmp_path, monkeypatch):
    venv, _python = _write_launcher_files(tmp_path, monkeypatch)
    execd = []
    argv = ["--venv", str(venv), "--no-launch"]

    code = start.main(argv, runner=_runner([]), execv=lambda prog, args: execd.append((prog, args)))

    assert code == start.EXIT_OK
    assert execd == []


def test_main_reports_missing_main_py(tmp_path, monkeypatch):
    venv, _python = _write_launcher_files(tmp_path, monkeypatch, make_main=False)
    execd = []
    argv = ["--venv", str(venv)]

    code = start.main(argv, runner=_runner([]), execv=lambda prog, args: execd.append((prog, args)))

    assert code == start.EXIT_ENTRY
    assert execd == []


def test_main_recreates_venv_when_interpreter_is_dead(tmp_path, monkeypatch):
    """自愈：venv 目录在、解释器不见了 → 自己重建，不用用户手删。"""
    venv = tmp_path / "venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("home = gone\n", encoding="utf-8")
    python = start.venv_python_path(venv)

    main_script = tmp_path / "main.py"
    main_script.write_text("print('hi')\n", encoding="utf-8")
    deps_script = tmp_path / "check_deps.py"
    deps_script.write_text("# stub\n", encoding="utf-8")
    monkeypatch.setattr(start, "MAIN_SCRIPT", main_script)
    monkeypatch.setattr(start, "DEPS_SCRIPT", deps_script)
    monkeypatch.setattr(start, "ROOT", tmp_path)
    monkeypatch.setattr(start.os, "chdir", lambda _path: None)

    calls = []

    def make_python():
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_bytes(b"")

    code = start.main(
        ["--venv", str(venv)],
        runner=_runner(calls, on_venv_create=make_python),
        execv=lambda prog, args: None,
    )

    assert code == start.EXIT_OK
    assert any("-m" in cmd and "venv" in cmd for cmd in calls), "应该调用 python -m venv 重建"


def test_main_reports_venv_creation_failure(tmp_path, monkeypatch):
    main_script = tmp_path / "main.py"
    main_script.write_text("print('hi')\n", encoding="utf-8")
    monkeypatch.setattr(start, "MAIN_SCRIPT", main_script)
    monkeypatch.setattr(start, "DEPS_SCRIPT", tmp_path / "check_deps.py")
    monkeypatch.setattr(start, "ROOT", tmp_path)
    monkeypatch.setattr(start.os, "chdir", lambda _path: None)

    def runner(cmd, **_kwargs):
        if "-m" in cmd and "venv" in cmd:
            return _Proc(1, stderr="No module named venv")
        return _Proc(0)

    code = start.main(["--venv", str(tmp_path / "venv")], runner=runner, execv=lambda prog, args: None)

    assert code == start.EXIT_VENV


def test_main_reports_missing_check_deps_script(tmp_path, monkeypatch):
    """check_deps.py 缺失时要指向"仓库拷贝不完整"，而不是抛栈。"""
    venv, _python = _write_launcher_files(tmp_path, monkeypatch)
    monkeypatch.setattr(start, "DEPS_SCRIPT", tmp_path / "gone.py")

    code = start.main(["--venv", str(venv)], runner=_runner([]), execv=lambda prog, args: None)

    assert code == start.EXIT_DEPS


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["--check"], True),
        (["--dev"], False),
        (["--force-launch"], False),
    ],
)
def test_parse_args_toggles(argv, expected):
    assert start.parse_args(argv).check is expected


# ==================== 批处理壳的编码守卫 ====================


def test_windows_wrappers_are_ascii_and_crlf():
    """``.bat`` 必须纯 ASCII + CRLF，否则 cmd.exe 直接报"不是内部或外部命令"。

    实测踩过：把 ``start.bat`` 写成 UTF-8（含中文注释）+ LF 换行后，双击运行得到

        '氦锛?rem' 不是内部或外部命令
        'em' 不是内部或外部命令
        此时不应有 &&。

    ——cmd.exe 按控制台代码页（中文系统 cp936）读 .bat，UTF-8 中文变成乱码**命令**，
    LF-only 又把 ``rem`` 行和括号块拆断。``.gitattributes`` 已固化
    ``*.bat text eol=crlf``，这里再加一层内容守卫，防止有人只改文件不管编码。
    面向用户的中文提示一律留在 ``start.py``（Python 自己处理编码）。
    """
    root = _repo_root()
    bats = sorted(root.glob("*.bat"))
    assert bats, "根目录应当有批处理启动壳"

    for path in bats:
        raw = path.read_bytes()
        assert all(byte < 128 for byte in raw), f"{path.name} 必须纯 ASCII（cmd.exe 按控制台代码页读 .bat）"
        assert b"\r\n" in raw, f"{path.name} 必须使用 CRLF 换行"
        assert b"\n" not in raw.replace(b"\r\n", b""), f"{path.name} 存在裸 LF 换行"
