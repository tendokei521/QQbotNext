"""修复历史坏装：把 `data/module/plugins/<name>/<wrapper>/…` 里的插件根上提一层。

背景：旧版 `install_from_zip` 只认"顶层目录名 == module.json.name"，GitHub 下载的
`<repo>-main/` 或带 `__MACOSX/` 的压缩包会被整包塞进 `plugins/<name>/`，形成
`plugins/demo/repo-main/main.py` 这种"装了但加载不了"的嵌套目录。

本脚本只做**幂等修复**：
- 目标目录里恰好只有一个子目录含 `main.py`/`module.json`（且自己不含）→ 把该子目录上提；
- 其它兄弟项（`__MACOSX`、docs 等）移进备份目录，不直接删；
- 不认识的结构原样跳过并打印原因；
- 默认 dry-run，确认无误后带 `--yes` 才落盘。

用法：
    python scripts/fix_module_layout.py --dry-run        # 只看会改什么（默认）
    python scripts/fix_module_layout.py --yes            # 实际修复
    python scripts/fix_module_layout.py --yes --root data/module/plugins
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = ROOT / "data" / "module" / "plugins"
ENTRY_FILES = ("main.py", "module.py")
MANIFEST_FILES = ("module.json",)
NOISE_DIRS = {"__MACOSX", "__pycache__", ".git", ".idea", ".vscode"}


def _is_plugin_root(path: Path) -> bool:
    return any((path / name).is_file() for name in (*ENTRY_FILES, *MANIFEST_FILES))


def _find_wrapper(module_dir: Path) -> tuple[Path | None, list[Path]]:
    """返回 (内层插件根, 需要挪进备份的兄弟项)；不可修复返回 (None, 原因列表)。"""
    if _is_plugin_root(module_dir):
        return None, []
    candidates = [
        p for p in sorted(module_dir.iterdir())
        if p.is_dir() and p.name not in NOISE_DIRS and _is_plugin_root(p)
    ]
    if len(candidates) != 1:
        return None, []
    inner = candidates[0]
    siblings = [p for p in sorted(module_dir.iterdir()) if p != inner]
    return inner, siblings


def plan(root: Path) -> list[dict]:
    actions: list[dict] = []
    if not root.is_dir():
        return actions
    for module_dir in sorted(root.iterdir()):
        if not module_dir.is_dir() or module_dir.name.startswith((".", "_")):
            continue
        if module_dir.name == ".repair-backup":
            continue
        inner, siblings = _find_wrapper(module_dir)
        if inner is None:
            if not _is_plugin_root(module_dir):
                actions.append({"module": module_dir.name, "action": "skip", "reason": "结构无法识别（无唯一内层插件根）"})
            continue
        actions.append({
            "module": module_dir.name,
            "action": "lift",
            "from": str(inner.name),
            "siblings": [p.name for p in siblings],
        })
    return actions


def apply(root: Path, actions: list[dict]) -> None:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_root = root / ".repair-backup" / stamp
    backup_root.mkdir(parents=True, exist_ok=True)
    for action in actions:
        if action["action"] != "lift":
            continue
        name = action["module"]
        module_dir = root / name
        inner = module_dir / action["from"]
        staging = root / f".lift-{name}-{stamp}"
        try:
            shutil.move(str(inner), str(staging))
            # 兄弟项（噪声/文档）挪进备份，先不删
            for sibling in [p for p in module_dir.iterdir()]:
                shutil.move(str(sibling), str(backup_root / name / sibling.name))
            module_dir.rmdir()
            shutil.move(str(staging), str(module_dir))
            print(f"[fix] {name}: 已上提 {action['from']}/ -> {name}/")
        except OSError as e:  # noqa: BLE001 —— 单模块失败不影响其它模块
            print(f"[fix] {name}: 修复失败（已跳过）: {e}")
            if staging.exists():
                shutil.move(str(staging), str(inner))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="修复旧版 zip 安装造成的嵌套插件目录")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="外部插件根目录")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划（默认行为）")
    parser.add_argument("--yes", action="store_true", help="实际执行修复")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出计划")
    args = parser.parse_args(argv)

    root = Path(args.root)
    actions = plan(root)
    if args.json:
        print(json.dumps({"root": str(root), "actions": actions}, ensure_ascii=False, indent=2))
    else:
        lift = [a for a in actions if a["action"] == "lift"]
        skip = [a for a in actions if a["action"] == "skip"]
        print(f"[fix] 扫描 {root}")
        for a in lift:
            print(f"  - 可修复 {a['module']}: {a['from']}/ -> 根（兄弟项: {a['siblings'] or '无'}）")
        for a in skip:
            print(f"  - 跳过 {a['module']}: {a['reason']}")
        if not actions:
            print("  （没有需要修复的目录）")

    if not args.yes:
        if any(a["action"] == "lift" for a in actions):
            print("[fix] dry-run：确认后加 --yes 执行")
        return 0

    apply(root, actions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
