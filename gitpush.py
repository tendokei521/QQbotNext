"""快捷 git add / commit / push 脚本。

提交信息采用 Conventional Commits（`<type>(<scope>): <subject>`，见 STYLE.md §11 与 AGENTS.md）。
本脚本按 STYLE.md §11「一步一提交」约定只提交**已暂存**的改动：
调用前请先用 `git add <相关文件>` 暂存本次步骤的文件，避免把无关改动混入同一提交。

用法：
    git add <文件...>
    venv\\Scripts\\python.exe gitpush.py "fix(llm): remove schedule fallback"
    # 或双击 gitpush.bat（无参数时会提示输入提交信息）
"""

from __future__ import annotations

import re
import subprocess
import sys

# Conventional Commits 校验：type(scope): subject，scope 可省略
_MESSAGE_RE = re.compile(
    r"^(feat|fix|docs|refactor|test|chore|perf|build|ci|style)"
    r"(\([A-Za-z0-9_./-]+\))?: \S.*$"
)


def run(cmd: list[str]) -> int:
    """执行命令并返回退出码（输出直通控制台）。"""
    print("$", " ".join(cmd))
    result = subprocess.run(cmd)
    return result.returncode


def build_message(argv: list[str]) -> str | None:
    """从命令行参数取提交信息；未提供时交互输入。返回 None 表示未提供。"""
    if len(argv) >= 2 and argv[1].strip():
        return argv[1].strip()
    try:
        entered = input("提交信息（Conventional Commits，如 fix(llm): remove schedule fallback）: ").strip()
    except EOFError:
        return None
    return entered or None


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    msg = build_message(sys.argv)
    if msg is None:
        print("[失败] 未提供提交信息。")
        return 1
    if not _MESSAGE_RE.match(msg):
        print(
            "[失败] 提交信息不符合 Conventional Commits：\n"
            "  期望 <type>(<scope>): <subject>，type ∈ "
            "feat/fix/docs/refactor/test/chore/perf/build/ci/style\n"
            f"  实际: {msg}"
        )
        return 1

    # 检查是否有已暂存改动，贯彻「一步一提交、不混入无关改动」
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"], capture_output=True, text=True
    )
    files = [f for f in staged.stdout.splitlines() if f.strip()]
    if not files:
        print("[失败] 没有已暂存的改动。请先 `git add <本次步骤的相关文件>`（勿用 git add -A 混入无关改动）。")
        return 1
    print("本次提交包含的已暂存文件：")
    for f in files:
        print(f"  - {f}")
    print()

    steps = [
        ("1/2 git commit", ["git", "commit", "-m", msg]),
        ("2/2 git push", ["git", "push", "origin", "main"]),
    ]
    for label, cmd in steps:
        print(f"==================== {label} ====================")
        rc = run(cmd)
        if rc != 0:
            print(f"\n[失败] {label}（exit {rc}；可能没有改动，或网络/权限问题）")
            return rc
        print()

    print(f'完成: "{msg}" 已提交并推送')
    return 0


if __name__ == "__main__":
    sys.exit(main())
