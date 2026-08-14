"""快捷 git add / commit / push 脚本。

提交信息 = 当前时间（YYYY.M.D HH:MM，见 STYLE.md §11 提交规范），
一次性提交工作区全部改动并推送。

用法：
    venv\\Scripts\\python.exe gitpush.py      # 或双击 gitpush.bat
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime


def run(cmd: list[str]) -> int:
    """执行命令并返回退出码（输出直通控制台）。"""
    print("$", " ".join(cmd))
    result = subprocess.run(cmd)
    return result.returncode


def build_message() -> str:
    """提交信息：YYYY.M.D HH:MM（月/日无前导零，与 STYLE.md 一致）。"""
    now = datetime.now()
    return f"{now.year}.{now.month}.{now.day} {now.hour:02d}:{now.minute:02d}"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    msg = build_message()
    print(f"提交信息: {msg}")
    print()

    steps = [
        ("1/3 git add -A", ["git", "add", "-A"]),
        ("2/3 git commit", ["git", "commit", "-m", msg]),
        ("3/3 git push", ["git", "push", "origin", "main"]),
    ]
    for label, cmd in steps:
        print(f"==================== {label} ====================")
        rc = run(cmd)
        if rc != 0:
            print(f"\n[失败] {label}（exit {rc}；可能没有改动，或网络/权限问题）")
            return rc
        print()

    print(f"完成: \"{msg}\" 已提交并推送")
    return 0


if __name__ == "__main__":
    sys.exit(main())
