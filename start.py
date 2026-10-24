#!/usr/bin/env python
"""QQBot Next 一键启动 / 部署：建 venv → 查依赖 → 启动。

一条命令把"这台机器到底能不能跑"从头管到底::

    python start.py                 # 一键：建 venv → 升 pip/setuptools → 查/装依赖 → 启动
    python start.py --check         # 只检查依赖，不装也不启动（退出码 1 = 不齐）
    python start.py --no-launch     # 把环境备齐但不起进程
    python start.py --recreate      # 删掉重建 venv（venv 坏掉时的自救入口）
    python start.py --dev           # 连带 [dev] 组（pytest / ruff ...）
    python start.py --venv PATH     # 用指定目录当 venv（默认 ./venv）
    python start.py --pause         # 结束前停一下，方便双击运行时看输出

退出码：``0`` 已启动／检查通过、``1`` venv 建不出来或不可用、``2`` 依赖没齐（因此**不**启动）、
``3`` 缺少 ``main.py``。

为什么本文件只准用标准库
------------------------
它的存在前提就是"这台机器上除 Python 什么都没有"，所以它自己一个第三方包都不能依赖。
旧版 ``start.bat`` 在这里翻过车：它调 ``scripts/ops/check_deps.py`` 做自检，而那个脚本
顶层 ``import packaging``；全新 venv（Python 3.10 + pip 21.2.3 + setuptools 57.4.0）的
site-packages 里没有独立的 ``packaging``，于是**自检脚本自己先崩了**，一行装包代码都没跑
到。用户看到的是 ``No module named 'packaging'`` 紧跟 ``No module named 'pydantic'``——
不是缺依赖，是"修依赖的那一环"先死了。

所以这里的顺序被刻意拆成四步，且每一步都只依赖"上一步已确保存在"的东西：

1. **interpreter**：``./venv`` 里那个解释器能 ``import sys`` 吗？不能就（重）建。
   判活而不是判存在——venv 目录在、但底座 Python 被卸载或 pyvenv.cfg 指向已失效的
   路径时，目录仍然存在，Python 却起不来。
2. **toolchain**：先把 venv 的 pip / setuptools / wheel 升上去。三个都是 venv 自带
   （ensurepip 塞进去的），升级失败只警告不致命——离线时它照样能用旧 pip 装 wheel。
3. **dependencies**：交给 ``check_deps.py``（同为根目录文件）比对 ``pyproject.toml``，
   缺什么装什么。判定逻辑只有那一份，bat / sh 不再各写一套。
4. **launch**：**依赖齐了才** ``exec`` 进 ``main.py``。旧版"缺依赖也继续启动"的做法
   等于把 Windows 上一屏 traceback 端给用户，这里改成明确失败并给下一步。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

#: 仓库根目录（本文件就在根目录）
ROOT = Path(__file__).resolve().parent
DEFAULT_VENV = ROOT / "venv"
MAIN_SCRIPT = ROOT / "main.py"
DEPS_SCRIPT = ROOT / "check_deps.py"
#: 启动检查通过后的缓存（删掉它等于强制下次全量检查）
CACHE_PATH = ROOT / "data" / ".launcher-cache.json"
#: 缓存格式版本：改了指纹算法就 +1，老缓存自动失效
CACHE_VERSION = 1
#: 缓存有效期上限（天）：环境可能被手工改动，定期强制复核一次
CACHE_MAX_AGE_DAYS = 14.0

IS_WINDOWS = os.name == "nt"

#: 探活 / 升工具链 / 装依赖的超时（装依赖下载慢，单独放宽）
PROBE_TIMEOUT = 120.0
TOOLCHAIN_TIMEOUT = 900.0
DEPS_TIMEOUT = 1800.0

EXIT_OK = 0
EXIT_VENV = 1
EXIT_DEPS = 2
EXIT_ENTRY = 3

Runner = Callable[..., Any]
Execv = Callable[[str, list[str]], None]


# ==================== 解释器与 venv ====================


def venv_python_path(venv_dir: Path) -> Path:
    """venv 内解释器的位置：Windows 在 ``Scripts/``，其它平台在 ``bin/``。"""
    return venv_dir / ("Scripts/python.exe" if IS_WINDOWS else "bin/python3")


def interpreter_works(python: Path, *, runner: Runner | None = None) -> bool:
    """跑一句 ``import sys`` 判活。

    只判 ``Path.exists()`` 不够：venv 目录残留但底座解释器已卸载、或 pyvenv.cfg 指向
    失效路径时，文件还在、进程起不来。这正是"重装过 Python 的系统上 venv 突然失联"
    的成因。
    """
    if not python.exists():
        return False
    runner = runner or subprocess.run
    try:
        done = runner([str(python), "-c", "import sys"], capture_output=True, timeout=PROBE_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return getattr(done, "returncode", 1) == 0


def base_python() -> str:
    """建 venv 用的解释器。

    当前进程若已经在某个 venv 里，取它的底座解释器（``sys.base_prefix``）——用 venv
    自己的 python 去建下一层 venv 会被 venv 模块拒绝。找得到底座就用底座，否则退回
    当前解释器，最后才去 PATH 上碰运气。
    """
    if sys.prefix != sys.base_prefix:
        candidate = Path(sys.base_prefix) / ("python.exe" if IS_WINDOWS else "bin/python3")
        if candidate.exists():
            return str(candidate)
    if sys.executable:
        return sys.executable
    fallback = shutil.which("python3") or shutil.which("python")
    if fallback:
        return fallback
    raise RuntimeError("找不到可用的 Python 解释器")


def remove_venv(venv_dir: Path) -> tuple[bool, str]:
    """删掉 venv 目录（``--recreate`` 用）。

    只删"看起来确实是 venv"的目录：有 ``pyvenv.cfg``，或干脆是空目录。防止 ``--venv``
    手滑指向一个真实项目目录时被整棵删掉。
    """
    if not venv_dir.exists():
        return True, "nothing to remove"
    looks_like_venv = (venv_dir / "pyvenv.cfg").exists()
    empty = not any(venv_dir.iterdir())
    if not (looks_like_venv or empty):
        return False, f"{venv_dir} 既不像 venv 也不为空，拒绝删除"
    try:
        shutil.rmtree(venv_dir)
    except OSError as e:
        return False, f"删除 {venv_dir} 失败: {e}"
    return True, "removed"


def create_venv(venv_dir: Path, *, runner: Runner | None = None) -> tuple[bool, str]:
    """用底座解释器建 venv。返回 ``(是否成功, 失败原因)``，不抛异常。"""
    runner = runner or subprocess.run
    try:
        cmd = [base_python(), "-m", "venv", str(venv_dir)]
    except RuntimeError as e:
        return False, str(e)
    try:
        done = runner(cmd, capture_output=True, text=True, timeout=TOOLCHAIN_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"调用 {cmd[0]} -m venv 失败: {e}"
    if getattr(done, "returncode", 1) != 0:
        lines = ((getattr(done, "stderr", "") or "") + (getattr(done, "stdout", "") or "")).strip().splitlines()
        return False, lines[-1] if lines else f"exit code {getattr(done, 'returncode', 1)}"
    return True, ""


# ==================== 工具链 ====================


def ensure_pip(python: Path, *, runner: Runner | None = None) -> tuple[bool, str]:
    """确保 venv 里有可用的 pip；没有就 ``ensurepip`` 补一份。"""
    runner = runner or subprocess.run
    for cmd in ([str(python), "-m", "pip", "--version"], [str(python), "-m", "ensurepip", "--upgrade"]):
        try:
            done = runner(cmd, capture_output=True, timeout=PROBE_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, f"pip 探活失败: {e}"
        if getattr(done, "returncode", 1) == 0:
            return True, ""
    return False, "venv 里没有 pip，ensurepip 也补不上；请删除 venv 后重跑 start.py"


def upgrade_toolchain(python: Path, *, runner: Runner | None = None, timeout: float = TOOLCHAIN_TIMEOUT) -> tuple[bool, str]:
    """升级 venv 的 pip / setuptools / wheel。

    失败**不致命**：离线时旧 pip 照样能装 wheel。但这一步不能省——裸 venv 自带的
    setuptools 57.4.0 早于 PEP 660，遇到 ``pyproject.toml`` 里的
    ``build-system requires = ["setuptools>=68"]`` 就得联网取新版构建后端。
    """
    runner = runner or subprocess.run
    cmd = [str(python), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"]
    try:
        done = runner(cmd, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"升级被跳过（{e}）"
    if getattr(done, "returncode", 1) != 0:
        return False, "升级失败（多半是没网 / 没配镜像），先用现有版本继续"
    return True, "pip / setuptools / wheel 已是最新"


# ==================== 依赖 ====================


def dependency_cmd(
    python: Path,
    *,
    check_only: bool = False,
    dev: bool = False,
    vector: bool = False,
    groups: Sequence[str] = (),
) -> list[str]:
    """拼出调用 ``check_deps.py`` 的命令行（纯函数，便于测试断言）。"""
    cmd = [str(python), str(DEPS_SCRIPT), "--quiet"]
    if check_only:
        cmd.append("--check")
    if dev:
        cmd.append("--dev")
    if vector:
        cmd.append("--vector")
    for group in groups:
        if group:
            cmd.extend(["--group", group])
    return cmd


def check_dependencies(
    python: Path,
    *,
    runner: Runner | None = None,
    check_only: bool = False,
    dev: bool = False,
    vector: bool = False,
    groups: Sequence[str] = (),
    timeout: float = DEPS_TIMEOUT,
) -> tuple[int, str]:
    """跑依赖自检（默认缺什么装什么）。返回 ``(退出码, 说明)``。

    输出直接透传给终端：pip 的下载进度、缺哪个包，用户要能当场看见。
    """
    runner = runner or subprocess.run
    cmd = dependency_cmd(python, check_only=check_only, dev=dev, vector=vector, groups=groups)
    try:
        done = runner(cmd, cwd=str(ROOT), timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return EXIT_DEPS, f"依赖自检没能跑起来: {e}"
    rc = getattr(done, "returncode", 1)
    if rc == 0:
        return EXIT_OK, "ok"
    return EXIT_DEPS, f"仍有依赖没装齐（check_deps.py 退出码 {rc}）"


# ==================== 检查结果缓存 ====================


def _file_digest(path: Path) -> str:
    """文件内容的 sha256 前 16 位；文件不存在给固定标记。"""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return "missing"


def _path_stamp(path: Path) -> str:
    """路径的 mtime + size（解释器被替换时它会变）。"""
    try:
        st = path.stat()
    except OSError:
        return "missing"
    return f"{int(st.st_mtime)}:{st.st_size}"


def _dir_stamp(path: Path) -> str:
    """目录 mtime（装/卸包会改它，读文件不会）。

    用纳秒精度：同一秒内装包时秒级时间戳可能不变，缓存就会误判为"没变"。
    """
    try:
        return str(path.stat().st_mtime_ns)
    except OSError:
        return "missing"


def site_packages_dir(python: Path) -> Path:
    """venv 的 site-packages 目录（用于感知手工装/卸包）。"""
    return python.parent.parent / ("Lib/site-packages" if IS_WINDOWS else "lib/site-packages")


def cache_key(
    python: Path,
    *,
    dev: bool = False,
    vector: bool = False,
    groups: Sequence[str] = (),
    python_version: str = "",
) -> dict:
    """算出本次环境指纹。任一项变化都会让旧缓存失效。"""
    return {
        "version": CACHE_VERSION,
        "python": str(python),
        "python_stamp": _path_stamp(python),
        "python_version": python_version,
        "site_packages": _dir_stamp(site_packages_dir(python)),
        "pyproject": _file_digest(ROOT / "pyproject.toml"),
        "requirements": _file_digest(ROOT / "requirements.txt"),
        "deps_script": _file_digest(DEPS_SCRIPT),
        "groups": sorted(["dev"] if dev else []) + sorted(["vector"] if vector else [])
        + sorted(str(g) for g in groups if g),
    }


def load_cache(path: Path | None = None) -> dict | None:
    """读缓存；格式不对/不存在返回 None。"""
    target = path or CACHE_PATH
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save_cache(record: dict, path: Path | None = None) -> bool:
    """写缓存（失败不致命：下次全量检查而已）。"""
    target = path or CACHE_PATH
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {**record, "checked_at": time.time()}
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        return True
    except OSError:
        return False


def clear_cache(path: Path | None = None) -> None:
    """删缓存（--recreate / 环境不可用时用）。"""
    try:
        (path or CACHE_PATH).unlink()
    except OSError:
        pass


def cache_matches(record: dict | None, key: dict) -> tuple[bool, str]:
    """缓存是否仍然有效。返回 (有效, 人类可读的原因)。"""
    if not record:
        return False, "首次运行，需要完整检查"
    if record.get("version") != key.get("version"):
        return False, "缓存格式已更新"
    for field, label in (
        ("python", "解释器路径变了"),
        ("python_stamp", "解释器被替换过"),
        ("python_version", "Python 版本变了"),
        ("site_packages", "依赖目录有变动（装/卸过包）"),
        ("pyproject", "pyproject.toml 改了"),
        ("requirements", "requirements.txt 改了"),
        ("deps_script", "check_deps.py 改了"),
        ("groups", "可选依赖组不同"),
    ):
        if record.get(field) != key.get(field):
            return False, label
    checked_at = float(record.get("checked_at") or 0)
    age_days = (time.time() - checked_at) / 86400.0
    if age_days > CACHE_MAX_AGE_DAYS:
        return False, f"缓存已过期（{age_days:.0f} 天未复核）"
    return True, ""


def _human_age(record: dict | None) -> str:
    if not record:
        return "刚刚"
    try:
        age = time.time() - float(record.get("checked_at") or 0)
    except (TypeError, ValueError):
        return "刚刚"
    if age < 90:
        return f"{int(age)} 秒前"
    if age < 5400:
        return f"{int(age / 60)} 分钟前"
    return f"{int(age / 3600)} 小时前"


def venv_python_version(python: Path, *, runner: Runner | None = None) -> str:
    """问一次 venv 解释器的版本（拿不到就返回空串 → 缓存必然不匹配）。"""
    runner = runner or subprocess.run
    try:
        done = runner(
            [str(python), "-c", "import sys;print('%d.%d.%d' % sys.version_info[:3])"],
            capture_output=True,
            timeout=PROBE_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if getattr(done, "returncode", 1) != 0:
        return ""
    out = getattr(done, "stdout", b"") or b""
    return out.decode("utf-8", "replace").strip() if isinstance(out, bytes) else str(out).strip()


# ==================== 启动 ====================


def launch_cmd(python: Path, extra: Sequence[str] = ()) -> list[str]:
    """拼出启动 ``main.py`` 的命令行（纯函数，便于测试断言）。"""
    return [str(python), str(MAIN_SCRIPT), *extra]


def launch(
    python: Path,
    *,
    extra: Sequence[str] = (),
    runner: Runner | None = None,
    execv: Execv | None = None,
    pause: bool = False,
    in_process: bool = False,
) -> int:
    """把进程交给 ``main.py``。

    默认用 ``execv`` 直接换掉本进程——这样 Ctrl+C、退出码、终端窗口都跟旧版
    ``start.bat`` 一致，不会多留一层父进程；``--pause`` 或测试注入时改走子进程。
    """
    runner = runner or subprocess.run
    cmd = launch_cmd(python, extra)
    if not (pause or in_process):
        execv = execv or os.execv
        os.chdir(ROOT)
        execv(cmd[0], cmd)
        return EXIT_OK  # execv 成功后不会走到这里
    done = runner(cmd, cwd=str(ROOT))
    code = getattr(done, "returncode", 1)
    if pause:
        try:
            input("\n按回车关闭窗口...")
        except (EOFError, KeyboardInterrupt):
            pass
    return code


# ==================== 输出 ====================


def _stage(index: int, total: int, label: str, detail: str) -> None:
    print(f"  [{index}/{total}] {label:<16}  {detail}", flush=True)


def _configure_stdio() -> None:
    """把 stdout/stderr 的编码错误降级为替换字符，并让非终端输出走 UTF-8。

    两件事都不能少：

    - 旧版把 Windows 控制台（cp936）逼出 ``UnicodeEncodeError`` 的那种崩法，不该在
      "启动脚本"这一层发生——它坏了就没人能启动了，所以 ``errors="replace"``；
    - 被重定向/被管道捕获时（CI、诊断脚本、``| tee``）改成 UTF-8，否则中文按 cp936
      写出去的字节到日志文件里就是乱码；真正的控制台（tty）则保留系统编码，cmd.exe
      双击运行时中文才正常。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream.isatty():
                stream.reconfigure(errors="replace")  # type: ignore[union-attr]
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, OSError, ValueError):
            pass


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="start.py",
        description="QQBot Next 一键启动：建 venv → 查/装依赖 → 启动 main.py",
    )
    parser.add_argument("--check", action="store_true", help="只检查依赖，不安装也不启动")
    parser.add_argument("--no-launch", action="store_true", help="备好环境但不起进程")
    parser.add_argument("--recreate", action="store_true", help="删掉并重建 venv")
    parser.add_argument("--dev", action="store_true", help="连带 [dev] 组（pytest / ruff ...）")
    parser.add_argument("--vector", action="store_true", help="连带 [vector] 组（sqlite-vec）")
    parser.add_argument("--group", action="append", default=[], help="额外可选组（可重复）")
    parser.add_argument("--venv", default=None, help="venv 目录（默认 ./venv）")
    parser.add_argument("--pause", action="store_true", help="结束前等待回车（双击运行时用）")
    parser.add_argument("--force-launch", action="store_true", help="依赖不齐也强行启动（不推荐）")
    parser.add_argument(
        "--recheck",
        action="store_true",
        help="忽略启动检查缓存，强制完整检查一遍（缓存可疑时用）",
    )
    parser.add_argument("extra", nargs=argparse.REMAINDER, help="原样传给 main.py 的参数")
    return parser.parse_args(argv)


# ==================== 主流程 ====================


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: Runner | None = None,
    execv: Execv | None = None,
    in_process: bool = False,
) -> int:
    args = parse_args(argv)
    runner = runner or subprocess.run
    total = 4
    venv_dir = Path(args.venv).expanduser().resolve() if args.venv else DEFAULT_VENV

    _configure_stdio()
    print()
    print("  QQBot Next  ::  launcher")
    print("  " + "-" * 58)

    python = venv_python_path(venv_dir)
    extra = [item for item in args.extra if item != "--"]

    # ---------- 缓存命中：环境没变就直接启动 ----------
    # 只缓存"结论"，钥匙是环境指纹（见 cache_key）。变一项就退回完整检查。
    key = cache_key(
        python,
        dev=args.dev,
        vector=args.vector,
        groups=args.group,
        python_version=venv_python_version(python, runner=runner) if python.exists() else "",
    )
    cached = None if (args.recheck or args.recreate) else load_cache()
    hit, why = cache_matches(cached, key)
    if hit and not args.check:
        print(f"  [cached] 环境已就绪（上次检查：{_human_age(cached)}）—— 跳过环境检查")
        if not MAIN_SCRIPT.exists():
            print()
            print(f"  [FAIL] 缺少 {MAIN_SCRIPT.name}，仓库拷贝不完整")
            return EXIT_ENTRY
        if args.no_launch:
            print()
            print("  环境就绪。启动用：python start.py")
            return EXIT_OK
        print()
        return launch(
            python, extra=extra, runner=runner, execv=execv, pause=args.pause, in_process=in_process
        )
    if cached is not None and not hit and not args.check:
        print(f"  [cache] 缓存失效：{why} —— 重新完整检查")

    # ---------- [1/4] 解释器 ----------
    if args.recreate:
        _stage(1, total, "interpreter", f"删除 {venv_dir}（--recreate）")
        clear_cache()
        ok, detail = remove_venv(venv_dir)
        if not ok:
            print(f"\n  [FAIL] {detail}")
            return EXIT_VENV
    if interpreter_works(python, runner=runner):
        _stage(1, total, "interpreter", str(python))
    else:
        _stage(1, total, "interpreter", f"创建 venv（首次运行）：{venv_dir}")
        ok, detail = create_venv(venv_dir, runner=runner)
        if not ok or not interpreter_works(python, runner=runner):
            print()
            print(f"  [FAIL] 建不出可用的 venv：{detail or '解释器起不来'}")
            print("         请确认已安装 Python 3.10+ 且在 PATH 上，然后重跑 python start.py")
            print("         手动入口：python -m venv venv")
            clear_cache()
            return EXIT_VENV

    # ---------- [2/4] 工具链 ----------
    ok, detail = ensure_pip(python, runner=runner)
    if not ok:
        print()
        print(f"  [FAIL] {detail}")
        return EXIT_VENV
    if args.check:
        # --check 承诺只读：不联网、不改 venv，快速回答"能不能跑"
        _stage(2, total, "toolchain", "跳过（--check 只做只读检查）")
    else:
        _, detail = upgrade_toolchain(python, runner=runner)
        _stage(2, total, "toolchain", detail)

    # ---------- [3/4] 依赖 ----------
    if not DEPS_SCRIPT.exists():
        print()
        print(f"  [FAIL] 缺少 {DEPS_SCRIPT.name}（依赖自检脚本），仓库拷贝不完整")
        print("         请重新完整拉取仓库，或先跑 requirements.bat / requirements.sh")
        return EXIT_DEPS
    rc, detail = check_dependencies(
        python,
        runner=runner,
        check_only=args.check,
        dev=args.dev,
        vector=args.vector,
        groups=args.group,
    )
    _stage(3, total, "dependencies", detail)
    if rc != EXIT_OK:
        print()
        print("  依赖不齐，先不启动（旧版「继续 anyway」的后果是启动时一屏 traceback）。")
        print("  常见原因：没联网 / 没配镜像。可尝试：")
        print("    python start.py                     # 联网后重跑，会自动补装")
        print("    requirements.bat  / requirements.sh # 走 requirements.txt 的兜底入口")
        print("    python check_deps.py --json         # 看到底缺哪几个")
        if not args.force_launch:
            return EXIT_DEPS
        print("  （--force-launch 已指定，继续尝试启动）")

    if args.check or args.no_launch:
        # 检查通过 → 记进缓存，下次打开就不用再查
        if rc == EXIT_OK:
            save_cache(cache_key(
                python,
                dev=args.dev,
                vector=args.vector,
                groups=args.group,
                python_version=venv_python_version(python, runner=runner),
            ))
        print()
        print("  环境就绪。启动用：python start.py" if rc == EXIT_OK else "")
        return EXIT_OK if rc == EXIT_OK else EXIT_DEPS

    # ---------- [4/4] 启动 ----------
    # 环境检查全过 → 缓存结论，下次直接启动
    save_cache(cache_key(
        python,
        dev=args.dev,
        vector=args.vector,
        groups=args.group,
        python_version=venv_python_version(python, runner=runner),
    ))
    if not MAIN_SCRIPT.exists():
        print()
        print(f"  [FAIL] 缺少 {MAIN_SCRIPT.name}，仓库拷贝不完整")
        return EXIT_ENTRY
    _stage(4, total, "launch", "main.py")
    print()
    return launch(python, extra=extra, runner=runner, execv=execv, pause=args.pause, in_process=in_process)


if __name__ == "__main__":
    sys.exit(main())
