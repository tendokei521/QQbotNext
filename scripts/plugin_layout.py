"""插件结构检查/迁移工具（与 `docs/PLUGIN_STYLE.md`、`tests/architecture/test_plugin_layout.py` 同一套规则）。

用法：
    python scripts/plugin_layout.py --check              # 列出不合规项
    python scripts/plugin_layout.py --check --json       # 机器可读
    python scripts/plugin_layout.py --apply --dry-run    # 只打印迁移计划（默认）
    python scripts/plugin_layout.py --apply --yes        # 真移动文件并改 import

规则（详见 docs/PLUGIN_STYLE.md）：
1. 根目录只放框架契约与人看的文件；实现文件进 `src/`（≤100 行且只被 main import 的可留根）。
2. `main.py` ≤220 行；`src/` 必须有 `__init__.py`，且不得反向 import main。
3. 插件内部一律相对导入：`from .src.xxx import ...`，禁止 `from app.plugins.<自己>.xxx`。

存量迁移用「棘轮」：`BASELINE_ROOT_MODULES` / `BASELINE_MISSING_INIT` 记录当前不合规项，
迁完一个删一条；闸门只允许基线内、且不允许长大。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_PLUGINS = ROOT / "app" / "plugins"
DATA_PLUGINS = ROOT / "data" / "module" / "plugins"
TESTS = ROOT / "tests"
TEMPLATE = ROOT / "docs" / "plugin-template"

#: 根目录允许的固定条目
ROOT_ALLOWED = {
    "module.json",
    "main.py",
    "__init__.py",
    "config_schema.py",
    "README.md",
    "requirements.txt",
    "pages",
    "src",
}
#: main.py 行数上限（超过说明非编排逻辑没抽走）
MAIN_MAX_LINES = 220
#: 可留根的实现模块：≤100 行且只被 main.py import
ROOT_PY_MAX_LINES = 100

#: 迁移棘轮：{插件: {根级实现文件: 迁移前起行数}}——迁进 src/ 后从这里删掉
BASELINE_ROOT_MODULES: dict[str, dict[str, int]] = {
}
#: 迁移棘轮：还没有 `__init__.py` 的插件
BASELINE_MISSING_INIT: set[str] = set()
#: 迁移棘轮：非 src/pages 的实现子目录
BASELINE_EXTRA_DIRS: dict[str, set[str]] = {}

#: 插件内部绝对导入（应当改相对导入）：{插件: {文件: 行数上限}}
BASELINE_ABSOLUTE_IMPORTS: dict[str, dict[str, int]] = {
}

_ABS_IMPORT = re.compile(r"^\s*from\s+app\.plugins\.(?P<plugin>[A-Za-z0-9_]+)\.(?P<rest>[A-Za-z0-9_.]+)\s+import")


@dataclass
class Issue:
    code: str
    plugin: str
    detail: str
    hint: str = ""

    def to_dict(self) -> dict:
        return {"code": self.code, "plugin": self.plugin, "detail": self.detail, "hint": self.hint}

    def __str__(self) -> str:
        hint = f" → {self.hint}" if self.hint else ""
        return f"[{self.code}] {self.plugin}: {self.detail}{hint}"


@dataclass
class Move:
    plugin: str
    source: str
    target: str
    line_count: int = 0
    plugins: list[str] = field(default_factory=list)  # 需要改 import 的文件（相对插件根）


def _py_lines(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
    except OSError:
        return 0


def _plugin_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        p for p in root.iterdir() if p.is_dir() and p.name != "__pycache__" and not p.name.startswith(".")
    )


def _root_modules(plugin: Path) -> dict[str, int]:
    """根级实现模块：非白名单的 .py（不含 main/__init__/config_schema）。"""
    out: dict[str, int] = {}
    for f in sorted(plugin.glob("*.py")):
        if f.name in {"main.py", "__init__.py", "config_schema.py"}:
            continue
        out[f.name] = _py_lines(f)
    return out


def _imported_by_main(plugin: Path, module: str) -> bool:
    main = plugin / "main.py"
    if not main.is_file():
        return False
    text = main.read_text(encoding="utf-8", errors="ignore")
    stem = module[:-3]
    return bool(
        re.search(rf"from\s+\.{re.escape(stem)}\s+import", text)
        or re.search(rf"from\s+\.{re.escape(stem)}\.import", text)
        or re.search(rf"import\s+{re.escape(stem)}\b", text)
    )


def scan(root: Path | None = None) -> list[Issue]:
    """按规则扫描，返回不合规项（使用迁移棘轮：基线内且未长大不报错）。"""
    root = root or APP_PLUGINS
    issues: list[Issue] = []
    for plugin in _plugin_dirs(root):
        # 规则 A：必需文件
        if not (plugin / "main.py").is_file():
            issues.append(Issue("A1", plugin.name, "缺少 main.py"))
            continue
        if not (plugin / "module.json").is_file():
            issues.append(Issue("A2", plugin.name, "缺少 module.json"))
        if not (plugin / "__init__.py").is_file() and plugin.name not in BASELINE_MISSING_INIT:
            issues.append(Issue("A3", plugin.name, "缺少 __init__.py（包语义/相对导入依赖它）"))

        # 规则 B：main.py 行数
        main_lines = _py_lines(plugin / "main.py")
        if main_lines > MAIN_MAX_LINES:
            issues.append(
                Issue("B1", plugin.name, f"main.py {main_lines} 行 > {MAIN_MAX_LINES}", "把非编排逻辑抽到 src/")
            )

        # 规则 C：根目录白名单 / 实现文件
        for entry in sorted(plugin.iterdir()):
            name = entry.name
            if name.startswith(".") or name == "__pycache__":
                continue
            if entry.is_dir():
                if name not in ROOT_ALLOWED and name not in BASELINE_EXTRA_DIRS.get(plugin.name, set()):
                    issues.append(Issue("C1", plugin.name, f"根级目录 {name}/ 不在白名单", "实现代码放 src/"))
                continue
            if name in ROOT_ALLOWED:
                continue
            if entry.suffix != ".py":
                issues.append(Issue("C2", plugin.name, f"根级文件 {name} 不在白名单", "实现/资源放 src/ 或 pages/"))
                continue
            # 根级 .py：小且只被 main 引用可留根，否则必须在棘轮基线内且不长大
            lines = _py_lines(entry)
            small_ok = lines <= ROOT_PY_MAX_LINES and _imported_by_main(plugin, name)
            if small_ok:
                continue
            baseline = BASELINE_ROOT_MODULES.get(plugin.name, {})
            limit = baseline.get(name)
            if limit is None:
                issues.append(
                    Issue("C3", plugin.name, f"根级实现文件 {name}（{lines} 行）应进 src/", f"移动到 src/{name}")
                )
            elif lines > limit:
                issues.append(
                    Issue("C4", plugin.name, f"{name} 从 {limit} 行涨到 {lines} 行", "迁移前禁止继续长大")
                )

        # 规则 D：src/ 卫生
        src = plugin / "src"
        if src.is_dir():
            if not (src / "__init__.py").is_file():
                issues.append(Issue("D1", plugin.name, "src/ 缺 __init__.py"))
            for f in src.rglob("*.py"):
                text = f.read_text(encoding="utf-8", errors="ignore")
                if re.search(r"^\s*from\s+\.\.*\s*main\s+import", text, re.M) or re.search(
                    r"^\s*import\s+\.\.*\s*main\b", text, re.M
                ):
                    issues.append(Issue("D2", plugin.name, f"src/{f.name} 反向 import main", "依赖方向只能 main → src"))

        # 规则 E：插件内部绝对导入（应用相对导入）
        baseline_abs = BASELINE_ABSOLUTE_IMPORTS.get(plugin.name, {})
        scan_files = sorted(plugin.glob("*.py"))
        if src.is_dir():
            scan_files += sorted(src.rglob("*.py"))
        for f in scan_files:
            if not f.is_file():
                continue
            count = 0
            for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                m = _ABS_IMPORT.match(line)
                if m and m.group("plugin") == plugin.name:
                    count += 1
            rel = f.relative_to(plugin).as_posix()
            limit = baseline_abs.get(rel, 0)
            if count > limit:
                issues.append(
                    Issue("E1", plugin.name, f"{rel} 有 {count} 处绝对自引用导入（上限 {limit}）", "改为 from .src.xxx import ...")
                )
    return issues


def plan_moves(root: Path | None = None, only: set[str] | None = None) -> list[Move]:
    """生成「根级实现文件 → src/」的迁移计划；``only`` 限定插件名。"""
    root = root or APP_PLUGINS
    moves: list[Move] = []
    for plugin in _plugin_dirs(root):
        if only and plugin.name not in only:
            continue
        for name, lines in _root_modules(plugin).items():
            if lines <= ROOT_PY_MAX_LINES and _imported_by_main(plugin, name):
                continue
            affected = []
            for f in plugin.rglob("*.py"):
                if f.name == name:
                    continue
                try:
                    text = f.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                stem = name[:-3]
                if re.search(rf"from\s+\.{re.escape(stem)}\b", text) or re.search(
                    rf"from\s+app\.plugins\.{re.escape(plugin.name)}\.{re.escape(stem)}\b", text
                ):
                    affected.append(f.relative_to(plugin).as_posix())
            moves.append(
                Move(
                    plugin=plugin.name,
                    source=name,
                    target=f"src/{name}",
                    line_count=lines,
                    plugins=sorted(affected),
                )
            )
    return moves


def _rewrite_plugin_imports(plugin_dir: Path, moved_stems: set[str]) -> list[str]:
    """把「被移动模块」的 import 改到新位置；按文件位置决定相对前缀。

    覆盖四种写法：
    - `from .<mod> import ...`（插件根 → `.src.<mod>`；src 内不变）
    - `from app.plugins.X.<mod> import ...`（根 → `.src.<mod>`；src 内 → `.<mod>`）
    - `from app.plugins.X import <mod>`（根 → `from .src import <mod>`；src 内 → `from . import <mod>`）
    - `import app.plugins.X.<mod> as alias`（根 → `from .src import <mod> as alias`；src 内 → `from . import <mod> as alias`）
    """
    changed: list[str] = []
    plugin_name = plugin_dir.name
    src_dir = plugin_dir / "src"
    for f in plugin_dir.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        in_src = src_dir in f.parents
        new = text
        for stem in moved_stems:
            esc = re.escape(stem)
            if in_src:
                new = re.sub(
                    rf"^(\s*from\s+)app\.plugins\.{re.escape(plugin_name)}\.{esc}\b",
                    rf"\1.{stem}",
                    new,
                    flags=re.M,
                )
            else:
                new = re.sub(rf"^(\s*from\s+)\.{esc}\b", rf"\1.src.{stem}", new, flags=re.M)
                new = re.sub(
                    rf"^(\s*from\s+)app\.plugins\.{re.escape(plugin_name)}\.{esc}\b",
                    rf"\1.src.{stem}",
                    new,
                    flags=re.M,
                )
        # `from app.plugins.X import a, b`：仅当名字全是被移动模块时改写
        def _pkg(match: re.Match, in_src: bool = in_src) -> str:
            names = [n.strip() for n in match.group("names").split(",")]
            if not names or any(n not in moved_stems for n in names):
                return match.group(0)
            head = "." if in_src else ".src"
            return f"{match.group('lead')}from {head} import {match.group('names')}"

        new = re.sub(
            rf"^(?P<lead>\s*)from\s+app\.plugins\.{re.escape(plugin_name)}\s+import\s+(?P<names>[A-Za-z0-9_, ]+)$",
            _pkg,
            new,
            flags=re.M,
        )
        # `import app.plugins.X.mod as alias`
        for stem in moved_stems:
            esc = re.escape(stem)
            head = "." if in_src else ".src"
            new = re.sub(
                rf"^(\s*)import\s+app\.plugins\.{re.escape(plugin_name)}\.{esc}\s+as\s+([A-Za-z0-9_]+)",
                rf"\1from {head} import {stem} as \2",
                new,
                flags=re.M,
            )
        if new != text:
            f.write_text(new, encoding="utf-8", newline="\n")
            changed.append(f.relative_to(plugin_dir).as_posix())
    return changed


def _rewrite_cross_plugin_refs(moves: list[Move]) -> list[str]:
    """改写「其它插件 / 框架代码」里对被移动模块的绝对引用（跨插件依赖保持绝对路径，只换位置）。"""
    changed: list[str] = []
    for move in moves:
        stem = move.source[:-3]
        old = f"app.plugins.{move.plugin}.{stem}"
        new = f"app.plugins.{move.plugin}.src.{stem}"
        for base in (ROOT / "app",):
            for f in base.rglob("*.py"):
                if f.parts and "src" in f.parts and f.parent.name == move.plugin:
                    continue  # 本插件内部已由 _rewrite_plugin_imports 处理
                text = f.read_text(encoding="utf-8", errors="ignore")
                if old not in text:
                    continue
                f.write_text(text.replace(old, new), encoding="utf-8", newline="\n")
                changed.append(f.relative_to(ROOT).as_posix())
    return sorted(set(changed))


def _rewrite_test_imports(moves: list[Move]) -> list[str]:
    changed: list[str] = []
    if not TESTS.is_dir():
        return changed
    for f in TESTS.rglob("*.py"):
        text = f.read_text(encoding="utf-8", errors="ignore")
        new = text
        for move in moves:
            stem = move.source[:-3]
            new = new.replace(
                f"app.plugins.{move.plugin}.{stem}",
                f"app.plugins.{move.plugin}.src.{stem}",
            )
            # 包式导入：`from app.plugins.X import mod` → `from app.plugins.X.src import mod`
            def _pkg_test(match: re.Match, stem: str = stem, plugin: str = move.plugin) -> str:
                names = [n.strip() for n in match.group("names").split(",")]
                if stem not in names:
                    return match.group(0)
                return f"from app.plugins.{plugin}.src import {match.group('names')}"

            new = re.sub(
                rf"^\s*from\s+app\.plugins\.{re.escape(move.plugin)}\s+import\s+(?P<names>[A-Za-z0-9_, ]+)$",
                _pkg_test,
                new,
                flags=re.M,
            )
        if new != text:
            f.write_text(new, encoding="utf-8", newline="\n")
            changed.append(f.relative_to(ROOT).as_posix())
    return changed


def apply_moves(root: Path | None = None, *, dry_run: bool = True, only: set[str] | None = None) -> dict:
    """执行迁移计划：移动文件 + 改插件内/测试里的 import。"""
    root = root or APP_PLUGINS
    moves = plan_moves(root, only=only)
    report = {"dry_run": dry_run, "moves": [m.__dict__ for m in moves], "changed": []}
    if dry_run or not moves:
        return report
    for move in moves:
        plugin_dir = root / move.plugin
        src = plugin_dir / "src"
        src.mkdir(exist_ok=True)
        if not (src / "__init__.py").is_file():
            (src / "__init__.py").write_text('"""插件实现细节（工具函数、领域逻辑）。由 main.py 导入。"""\n', encoding="utf-8")
        (plugin_dir / move.source).rename(src / Path(move.target).name)
    # 全部移动完再改 import：先移后改，才能按「文件现在的位置」决定相对前缀
    for plugin_name in sorted({m.plugin for m in moves}):
        stems = {Path(m.source).stem for m in moves if m.plugin == plugin_name}
        report["changed"].extend(_rewrite_plugin_imports(root / plugin_name, stems))
    report["changed"].extend(_rewrite_cross_plugin_refs(moves))
    report["changed"].extend(_rewrite_test_imports(moves))
    report["changed"] = sorted(set(report["changed"]))
    return report


def check_template() -> list[Issue]:
    """模板自身必须符合规范（它是新插件的起点）。"""
    issues: list[Issue] = []
    if not TEMPLATE.is_dir():
        return issues
    for required in ("module.json", "main.py", "__init__.py", "README.md", "config_schema.py", "src/__init__.py"):
        if not (TEMPLATE / required).is_file():
            issues.append(Issue("T1", "plugin-template", f"缺少 {required}"))
    main = TEMPLATE / "main.py"
    if main.is_file() and _py_lines(main) > 150:
        issues.append(Issue("T2", "plugin-template", "模板 main.py 超过 150 行，示例应保持精简"))
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="插件结构检查/迁移")
    parser.add_argument("--check", action="store_true", help="检查并列出问题")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument("--apply", action="store_true", help="执行迁移（默认 dry-run）")
    parser.add_argument("--dry-run", action="store_true", help="只打印迁移计划")
    parser.add_argument("--yes", action="store_true", help="确认真正移动文件")
    parser.add_argument("--plugin", action="append", default=[], help="只迁移指定插件（可重复）")
    args = parser.parse_args(argv)

    if args.apply:
        report = apply_moves(dry_run=not args.yes, only=set(args.plugin) or None)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    issues = scan() + check_template()
    if DATA_PLUGINS.is_dir():
        issues += scan(DATA_PLUGINS)
    if args.json:
        print(json.dumps([i.to_dict() for i in issues], ensure_ascii=False, indent=2))
    else:
        for issue in issues:
            print(issue)
        print(f"\n共 {len(issues)} 条；迁移计划：python scripts/plugin_layout.py --apply --dry-run")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
