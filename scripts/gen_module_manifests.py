"""为 `app/plugins/<name>/` 生成/补全 `module.json`（v2 清单）。

为什么用 AST 而不是 import：内置模块 import 时依赖 `app` 运行环境（容器/服务），
而清单生成本身只该读源码里的类属性。AST 静态解析 `class Module` 的常量赋值即可。

用法：
    python scripts/gen_module_manifests.py            # 只补缺失的
    python scripts/gen_module_manifests.py --force    # 全量重写（值仍来自源码/默认值）
    python scripts/gen_module_manifests.py --check    # 有缺失则退出码 1（CI 用）
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGINS_DIR = ROOT / "app" / "plugins"

#: 类属性 → 清单字段（display_name 取类上的 name）
ATTR_MAP = {
    "display_name": "name",
    "sign": "sign",
    "description": "description",
    "version": "version",
}
DEFAULT_VERSION = "1.0.0"
DEFAULT_AUTHOR = "tendokei521"
API_VERSION = 1


def _class_constant(node: ast.ClassDef, attr: str) -> object | None:
    """取类体里 `attr = <常量>` 的值（支持 str/int/list[str]），取不到返回 None。"""
    for stmt in node.body:
        if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
            continue
        target = stmt.targets[0]
        if not isinstance(target, ast.Name) or target.id != attr:
            continue
        try:
            return ast.literal_eval(stmt.value)
        except (ValueError, SyntaxError):
            return None
    return None


def read_class_metadata(module_dir: Path) -> dict:
    """从 main.py 的 `class Module` 里静态读元数据；失败返回空 dict。"""
    main_py = module_dir / "main.py"
    if not main_py.is_file():
        return {}
    try:
        tree = ast.parse(main_py.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return {}

    cls = next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Module"),
        None,
    )
    if cls is None:
        return {}

    attrs: dict = {}
    for field, attr in ATTR_MAP.items():
        value = _class_constant(cls, attr)
        if value is not None:
            attrs[field] = value
    return attrs


def build_manifest(module_dir: Path) -> dict:
    attrs = read_class_metadata(module_dir)
    return {
        "name": module_dir.name,
        "display_name": str(attrs.get("display_name") or module_dir.name),
        "sign": str(attrs.get("sign") or module_dir.name),
        "version": str(attrs.get("version") or DEFAULT_VERSION),
        "description": str(attrs.get("description") or ""),
        "author": DEFAULT_AUTHOR,
        "entry": "main.py",
        "api_version": API_VERSION,
    }


def is_module_dir(entry: Path) -> bool:
    return entry.is_dir() and not entry.name.startswith(("_", ".")) and (entry / "main.py").is_file()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成/补全内置模块的 module.json")
    parser.add_argument("--force", action="store_true", help="已存在也重写（值来自源码/默认值）")
    parser.add_argument("--check", action="store_true", help="只检查，缺失时退出码 1")
    parser.add_argument("--dir", default=str(PLUGINS_DIR), help="插件根目录")
    args = parser.parse_args(argv)

    plugins_dir = Path(args.dir)
    if not plugins_dir.is_dir():
        print(f"[gen] 插件目录不存在: {plugins_dir}", file=sys.stderr)
        return 2

    created: list[str] = []
    skipped: list[str] = []
    missing: list[str] = []
    for entry in sorted(plugins_dir.iterdir()):
        if not is_module_dir(entry):
            continue
        manifest_path = entry / "module.json"
        if manifest_path.is_file() and not args.force:
            skipped.append(entry.name)
            continue
        if args.check:
            missing.append(entry.name)
            continue
        data = build_manifest(entry)
        manifest_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        created.append(entry.name)

    if args.check:
        if missing:
            print(f"[gen] 缺少 module.json: {', '.join(missing)}")
            return 1
        print(f"[gen] 全部 {len(skipped)} 个模块都有 module.json")
        return 0

    if created:
        print(f"[gen] 写入 {len(created)} 个: {', '.join(created)}")
    if skipped:
        print(f"[gen] 已存在跳过 {len(skipped)} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
