#!/usr/bin/env python
"""构建「解压即用」发行包（Deploy Kit）——计划 P5-4。

目标：把"装 Python → 建 venv → 装依赖 → 构建前端"四步压成"解压双击"。
产物是一个 zip，里面固定有：

    QQBotNext/
      start.sh / start.py         启动入口（逻辑全在 start.py；Windows 用 python start.py）
      start.py / check_deps.py    一键启动与依赖自检
      main.py / pyproject.toml
      app/                        后端源码 + 已构建的 dashboard/dist
      wheelhouse/                 可选：离线依赖轮子（--with-wheelhouse）
      bundles/                    可选：默认预设（导入即可用）
      deploy.json                 版本、构建时间、文件清单与 sha256
      README-FIRST.txt            三步上手说明（给人看的，不是给机器）

设计约束（与 P0 一脉相承）：
- **不含用户数据**：``venv/``、``data/``、``logs/``、``.env``、缓存一律排除，
  否则"发出去的包带着别人的 Key / 聊天记录"；
- **可复现**：文件顺序、时间戳固定，同输入同字节（便于发布校验）；
- **可核对**：``deploy.json`` 记录每个文件的 sha256，解压后可自查完整性。

用法::

    python scripts/build_deploy_kit.py                  # 产物到 dist/kit/
    python scripts/build_deploy_kit.py --with-wheelhouse # 连离线依赖一起打（要联网）
    python scripts/build_deploy_kit.py --embed-python python-3.10-embed.zip
    python scripts/build_deploy_kit.py --version 2.0.1
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 绝不进包：用户数据、环境、缓存、VCS、构建中间物
#: 注意：``dist`` 只排除**仓库根目录**的构建产物——
#: ``app/webui/dashboard/dist`` 是前端产物，必须进包（否则打开是"请先 pnpm build"页）。
EXCLUDE_DIRS = {
    ".git", ".github", "venv", ".venv", "data", "logs", "node_modules",
    "__pycache__", ".pytest_cache", ".ruff_cache", ".idea", ".vscode",
}
ROOT_ONLY_EXCLUDE_DIRS = {"dist", "build"}
EXCLUDE_FILES = {
    ".env", "pytest.ini", ".coverage", ".gitmessage",
}
EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".log", ".db", ".db-wal", ".db-shm")

#: 顶层必须存在的入口（缺任何一个，包都不可用 → 直接失败）
REQUIRED_ENTRIES = ("start.sh", "start.py", "check_deps.py", "main.py", "pyproject.toml")
REQUIRED_DIRS = ("app",)

#: 前端必须已构建（未构建的包打开是"请先 pnpm build"提示页）
DIST_MARKER = Path("app/webui/dashboard/dist/index.html")

README_FIRST = """QQBot Next - 免安装发行包

三步上手
--------
1. Windows 双击 `start.py`（或命令行 python start.py）；macOS/Linux 执行 ./start.sh。
   首次启动会自动建 venv、装依赖（离线包会直接用 wheelhouse/ 里的轮子）。
2. 浏览器打开 http://127.0.0.1:8080 ，进入「首次配置」向导：
   连接账号 -> 配置模型 -> 写人设 -> 完成检查。
3. 在群里 @ 机器人试一句。没反应就看总览页「运行状态」卡，它会告诉你差什么。

需要准备什么
------------
- 一个 QQ 号（协议端 NapCat / Lagrange 负责登录，本程序只通过 WebSocket 连接）。
- 模型 API Key（OpenAI 兼容 / Anthropic / Gemini 均可）。

数据在哪
--------
data/（数据库与配置）、logs/（日志）都在本目录下，**升级时不要删**；
换机迁移用「预设中心」导出/导入。

出问题先看
----------
- 控制台退出码：1=venv、2=依赖、3=缺 main.py、4=Python 版本不满足。
- 总览页「运行状态」卡 / GET /api/readiness：逐条列出"差什么、去哪修"。
- deploy.json 里有每个文件的 sha256，可核对包是否完整。
"""


def _is_excluded(relative: Path) -> bool:
    parts = relative.parts
    if parts and parts[0] in ROOT_ONLY_EXCLUDE_DIRS:
        return True
    if any(part in EXCLUDE_DIRS for part in parts[:-1]):
        return True
    if relative.name in EXCLUDE_FILES:
        return True
    return relative.name.endswith(EXCLUDE_SUFFIXES)


def collect_files(root: Path) -> list[Path]:
    """收集要进包的文件（相对 root，已排序，排除用户数据/环境/缓存）。"""
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if _is_excluded(relative):
            continue
        files.append(relative)
    return files


def validate_tree(root: Path) -> None:
    """打包前自检：入口齐、前端已构建。缺一个就明确报错，不产出半成品。"""
    missing = [name for name in REQUIRED_ENTRIES if not (root / name).is_file()]
    if missing:
        raise SystemExit(f"[build_deploy_kit] 缺少入口文件：{', '.join(missing)}")
    missing_dirs = [name for name in REQUIRED_DIRS if not (root / name).is_dir()]
    if missing_dirs:
        raise SystemExit(f"[build_deploy_kit] 缺少目录：{', '.join(missing_dirs)}")
    if not (root / DIST_MARKER).is_file():
        raise SystemExit(
            "[build_deploy_kit] 前端未构建：缺少 app/webui/dashboard/dist/index.html\n"
            "  先执行：cd app/webui/dashboard && pnpm install && pnpm build"
        )


def _write_zip(files: dict[str, bytes], out_path: Path) -> None:
    """确定性 zip：固定时间戳 + 排序写入。"""
    fixed = (2020, 1, 1, 0, 0, 0)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=fixed)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, files[name])
    out_path.write_bytes(buffer.getvalue())


def _download_wheelhouse(target: Path) -> list[str]:
    """把运行期依赖下载成 wheel（需要联网；失败即报错，不静默跳过）。"""
    target.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, "-m", "pip", "download",
        "--dest", str(target),
        "--only-binary=:all:",
        "--no-cache-dir",
        "-e", ".",  # 让 pip 解析 pyproject.toml 的运行期依赖
    ]
    # `pip download -e .` 在部分 pip 版本上不支持；退回按项目根目录解析
    command = command[:-2] + [".", ]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(
            "[build_deploy_kit] 下载依赖失败（需要联网或镜像）：\n"
            + (result.stderr or result.stdout).strip()[-800:]
        )
    return sorted(item.name for item in target.iterdir())


def _collect_starter_bundles(root: Path) -> dict[str, bytes]:
    """可选：``bundles/`` 下的默认预设（仓库里已导出的 .zip 原样带进包）。"""
    source = root / "bundles"
    payloads: dict[str, bytes] = {}
    if not source.is_dir():
        return payloads
    for item in sorted(source.glob("*.zip")):
        payloads[f"bundles/{item.name}"] = item.read_bytes()
    return payloads


def build_kit(
    root: Path = ROOT,
    *,
    out_dir: Path | None = None,
    version: str = "dev",
    with_wheelhouse: bool = False,
    embed_python: Path | None = None,
    top_name: str = "QQBotNext",
) -> Path:
    validate_tree(root)
    out_dir = Path(out_dir or root / "dist" / "kit")

    payloads: dict[str, bytes] = {}
    checksums: dict[str, str] = {}
    for relative in collect_files(root):
        data = (root / relative).read_bytes()
        payloads[f"{top_name}/{relative.as_posix()}"] = data
        checksums[relative.as_posix()] = hashlib.sha256(data).hexdigest()

    payloads.update({f"{top_name}/{name}": data for name, data in _collect_starter_bundles(root).items()})
    payloads[f"{top_name}/README-FIRST.txt"] = README_FIRST.encode("utf-8")

    if with_wheelhouse:
        wheel_dir = root / "dist" / "_wheelhouse"
        if wheel_dir.exists():
            shutil.rmtree(wheel_dir)
        names = _download_wheelhouse(wheel_dir)
        for name in names:
            data = (wheel_dir / name).read_bytes()
            payloads[f"{top_name}/wheelhouse/{name}"] = data
            checksums[f"wheelhouse/{name}"] = hashlib.sha256(data).hexdigest()

    if embed_python is not None:
        embed_python = Path(embed_python)
        if not embed_python.is_file():
            raise SystemExit(f"[build_deploy_kit] 找不到内嵌 Python 包：{embed_python}")
        data = embed_python.read_bytes()
        payloads[f"{top_name}/python-embed.zip"] = data
        checksums["python-embed.zip"] = hashlib.sha256(data).hexdigest()

    manifest = {
        "schema": "qqbotnext.deploykit/1",
        "version": version,
        "built_at": int(time.time()),
        "python": "3.10",
        "offline": bool(with_wheelhouse),
        "embedded_python": bool(embed_python),
        "files": checksums,
        "count": len(checksums),
    }
    payloads[f"{top_name}/deploy.json"] = json.dumps(
        manifest, ensure_ascii=False, indent=2
    ).encode("utf-8")

    safe_version = "".join(ch for ch in version if ch.isalnum() or ch in "._-") or "dev"
    out_path = Path(out_dir) / f"QQBotNext-{safe_version}-kit.zip"
    _write_zip(payloads, out_path)
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建 QQBot Next 免安装发行包")
    parser.add_argument("--out", default=None, help="输出目录（默认 dist/kit）")
    parser.add_argument("--version", default="dev", help="版本号（写进 deploy.json 与文件名）")
    parser.add_argument("--with-wheelhouse", action="store_true", help="打包离线依赖轮子（需要联网）")
    parser.add_argument("--embed-python", default=None, help="可选：内嵌 Python 压缩包路径")
    args = parser.parse_args(argv)

    out_path = build_kit(
        ROOT,
        out_dir=Path(args.out) if args.out else None,
        version=args.version,
        with_wheelhouse=args.with_wheelhouse,
        embed_python=Path(args.embed_python) if args.embed_python else None,
    )
    size = out_path.stat().st_size
    print(f"[build_deploy_kit] 产物：{out_path}（{size / 1024 / 1024:.1f} MB）")
    print("[build_deploy_kit] 解压后运行 start.py 即可；deploy.json 内含逐文件 sha256。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
