"""标准发布脚本：版本校验、质量门禁、提交打 tag、推送并创建 GitHub Release。

用法：
    python scripts/release.py --version 2.0.1
    python scripts/release.py --version v2.0.1 --skip-dashboard --skip-tests

功能：
- 自动同步 pyproject.toml 与 dashboard/package.json 的版本号；
- 运行 pytest 与 Dashboard 构建作为发布前门禁；
- 提交发布改动、打 annotated tag 并推送 main + tag；
- 优先使用 gh CLI，否则用 Windows 凭据管理器 / GH_TOKEN 调 GitHub API 创建 Release。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
DASHBOARD_PKG = ROOT / "dashboard" / "package.json"
CHANGELOG = ROOT / "CHANGELOG.md"
DEFAULT_REPO = "tendokei521/QQbotNext"

SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
PYPROJECT_VERSION_RE = re.compile(r'(?m)^version\s*=\s*"([^"]+)"')


def log(message: str) -> None:
    print(message, flush=True)


def run(cmd: list[str], cwd: Path | None = None) -> None:
    log("$ " + " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def normalize_version(raw: str) -> str:
    m = SEMVER_RE.match(raw)
    if not m:
        raise SystemExit(f"无效版本号：{raw}，请使用 X.Y.Z 或 vX.Y.Z")
    return f"v{m.group(1)}.{m.group(2)}.{m.group(3)}"


def version_without_v(tag: str) -> str:
    return tag[1:] if tag.startswith("v") else tag


def write_pyproject_version(version: str) -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    new_text = PYPROJECT_VERSION_RE.sub(f'version = "{version}"', text, count=1)
    if new_text == text:
        log("pyproject.toml 版本已是目标版本，跳过")
    else:
        PYPROJECT.write_text(new_text, encoding="utf-8")
        log(f"pyproject.toml 版本已更新为 {version}")


def write_dashboard_version(version: str) -> None:
    if not DASHBOARD_PKG.exists():
        log("dashboard/package.json 不存在，跳过 Dashboard 版本同步")
        return
    data = json.loads(DASHBOARD_PKG.read_text(encoding="utf-8"))
    if data.get("version") == version:
        log("dashboard/package.json 版本已是目标版本，跳过")
        return
    data["version"] = version
    DASHBOARD_PKG.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log(f"dashboard/package.json 版本已更新为 {version}")


def check_clean(allow_dirty: bool) -> None:
    if allow_dirty:
        log("已跳过工作区干净检查（--allow-dirty）")
        return
    out = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    if out:
        raise SystemExit("工作区不干净，请先提交无关改动；或使用 --allow-dirty 强制继续")


def ensure_changelog(version: str, notes_file: str | None) -> Path:
    if notes_file:
        notes_path = Path(notes_file)
        if not notes_path.exists():
            raise SystemExit(f"Release Notes 文件不存在：{notes_path}")
        return notes_path
    if not CHANGELOG.exists():
        raise SystemExit("CHANGELOG.md 不存在；请先创建并补充当前版本条目，或使用 --notes-file")
    text = CHANGELOG.read_text(encoding="utf-8")
    if f"## [{version_without_v(version)}]" not in text:
        raise SystemExit(f"CHANGELOG.md 缺少 [ {version_without_v(version)} ] 条目；请先补充")
    return CHANGELOG


def run_tests() -> None:
    log("=== 运行后端测试 ===")
    run([sys.executable, "-m", "pytest", "-q"])


def run_dashboard_build() -> None:
    log("=== 构建 Dashboard ===")
    pnpm = shutil.which("pnpm")
    if not pnpm:
        log("未找到 pnpm，跳过 Dashboard 构建（可用 --skip-dashboard 显式跳过）")
        return
    run([pnpm, "build"], cwd=ROOT / "dashboard")


def git(*args: str) -> None:
    run(["git", *args], cwd=ROOT)


def commit_release(tag: str) -> None:
    log("=== 提交发布改动 ===")
    git("add", "-A")
    git("commit", "-m", f"chore(release): {tag}")


def tag_release(tag: str) -> None:
    log("=== 创建 tag ===")
    existing = subprocess.run(
        ["git", "tag", "-l", tag], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    if existing:
        log(f"tag {tag} 已存在，跳过创建")
        return
    git("tag", "-a", tag, "-m", f"Release {tag}")


def push_release(tag: str) -> None:
    log("=== 推送 main 与 tag ===")
    git("push", "origin", "HEAD")
    git("push", "origin", tag)


def get_github_token() -> str:
    for key in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN"):
        token = os.environ.get(key)
        if token:
            return token.strip()
    proc = subprocess.run(
        ["git", "credential", "fill"],
        cwd=ROOT,
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError("无法从环境变量或 git credential 获取 GitHub Token")
    for line in proc.stdout.splitlines():
        if line.startswith("password="):
            return line[len("password="):].strip()
    raise RuntimeError("git credential 未返回 GitHub Token")


def create_release_with_gh(tag: str, notes_path: Path, repo: str) -> None:
    run(
        [
            "gh",
            "release",
            "create",
            tag,
            "--repo",
            repo,
            "--title",
            tag,
            "--notes-file",
            str(notes_path),
        ],
        cwd=ROOT,
    )


def create_release_with_api(tag: str, notes_path: Path, repo: str) -> None:
    log("=== 通过 GitHub API 创建 Release ===")
    body = notes_path.read_text(encoding="utf-8")
    payload = json.dumps(
        {
            "tag_name": tag,
            "name": tag,
            "body": body,
            "draft": False,
            "prerelease": False,
        },
        ensure_ascii=False,
    )
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases",
        data=payload.encode("utf-8"),
        headers={
            "Authorization": f"Bearer {get_github_token()}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            log(f"Release 已创建：{data['html_url']}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise SystemExit(f"GitHub API 创建 Release 失败（{e.code}）：{detail}") from e


def create_release(tag: str, notes_path: Path, repo: str) -> None:
    if shutil.which("gh"):
        create_release_with_gh(tag, notes_path, repo)
        return
    create_release_with_api(tag, notes_path, repo)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="QQBot Next 标准发布脚本")
    parser.add_argument("--version", required=True, help="目标版本，如 2.0.1 或 v2.0.1")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="GitHub 仓库，如 owner/repo")
    parser.add_argument("--notes-file", help="Release Notes 文件路径；缺省使用 CHANGELOG.md")
    parser.add_argument("--skip-tests", action="store_true", help="跳过 pytest")
    parser.add_argument("--skip-dashboard", action="store_true", help="跳过 Dashboard 构建")
    parser.add_argument("--skip-push", action="store_true", help="不推送 main/tag")
    parser.add_argument("--skip-release", action="store_true", help="不创建 GitHub Release")
    parser.add_argument("--allow-dirty", action="store_true", help="允许非干净工作区发布")
    return parser.parse_args()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    args = parse_args()
    tag = normalize_version(args.version)
    version = version_without_v(tag)

    check_clean(args.allow_dirty)
    write_pyproject_version(version)
    write_dashboard_version(version)
    notes_path = ensure_changelog(version, args.notes_file)

    if not args.skip_tests:
        run_tests()
    if not args.skip_dashboard:
        run_dashboard_build()

    commit_release(tag)
    tag_release(tag)
    if not args.skip_push:
        push_release(tag)
    if not args.skip_release:
        create_release(tag, notes_path, args.repo)

    log(f"发布完成：{tag} -> https://github.com/{args.repo}/releases/tag/{tag}")


if __name__ == "__main__":
    main()