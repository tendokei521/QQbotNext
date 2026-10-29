"""闸门：插件目录结构与「main.py 只做编排」的边界。

规则见 `docs/PLUGIN_STYLE.md`；检查逻辑与 `scripts/plugin_layout.py --check` 共用一份
（`scripts/plugin_layout.py::scan`），避免文档、脚本、测试三处各写一套。

为什么需要这道闸门：插件最容易长成「一个 main.py 什么都有」或「工具函数散落根目录」，
代码评审靠人盯不住；把可判定的部分自动化后，迁移只需删棘轮基线，规则只会收紧。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_layout_tool():
    """按文件路径加载 scripts/plugin_layout.py。

    pytest 的导入根与脚本目录不一定在 sys.path 里（CI 与本地不同），按路径加载
    比依赖 `sys.path` 更稳；同时保证测试与 `--check` 用的是同一份规则。
    """
    spec = importlib.util.spec_from_file_location(
        "plugin_layout", ROOT / "scripts" / "plugin_layout.py"
    )
    assert spec and spec.loader, "找不到 scripts/plugin_layout.py"
    module = importlib.util.module_from_spec(spec)
    # dataclass 在 exec 时会用 sys.modules[cls.__module__]，先登记再执行
    import sys

    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_LAYOUT = _load_layout_tool()
APP_PLUGINS = _LAYOUT.APP_PLUGINS
BASELINE_ABSOLUTE_IMPORTS = _LAYOUT.BASELINE_ABSOLUTE_IMPORTS
BASELINE_EXTRA_DIRS = _LAYOUT.BASELINE_EXTRA_DIRS
BASELINE_MISSING_INIT = _LAYOUT.BASELINE_MISSING_INIT
BASELINE_ROOT_MODULES = _LAYOUT.BASELINE_ROOT_MODULES
TEMPLATE = _LAYOUT.TEMPLATE
check_template = _LAYOUT.check_template
scan = _LAYOUT.scan


def _format(issues) -> str:
    return "\n  ".join(f"[{i.code}] {i.plugin}: {i.detail}" + (f" → {i.hint}" if i.hint else "") for i in issues)


def test_repo_plugin_layout_is_clean() -> None:
    """仓库内插件：根目录白名单、main.py ≤220 行、src/ 方向、相对导入。"""
    issues = scan(APP_PLUGINS)
    assert not issues, (
        "这些插件不符合 docs/PLUGIN_STYLE.md（迁移中的存量见 scripts/plugin_layout.py 的 BASELINE_*）：\n  "
        + _format(issues)
    )


def test_plugin_template_is_usable() -> None:
    """模板本身必须合规：新插件以它为起点，模板不规范等于例外永远存在。"""
    issues = check_template()
    assert not issues, "插件模板不合规（docs/plugin-template/）：\n  " + _format(issues)
    for name in ("module.json", "main.py", "__init__.py", "README.md", "config_schema.py", "src/__init__.py"):
        assert (TEMPLATE / name).is_file(), f"模板缺少 {name}"
    assert (TEMPLATE / "src" / "reply.py").is_file(), "模板应演示「纯函数放 src/」"


def test_plugin_baseline_tables_have_no_stale_entries() -> None:
    """棘轮：基线里列着但磁盘上已经不存在的条目必须删掉，否则基线会越背越多。"""
    stale: list[str] = []
    for plugin, files in BASELINE_ROOT_MODULES.items():
        for filename in files:
            if not (APP_PLUGINS / plugin / filename).is_file():
                stale.append(f"{plugin}/{filename}")
    for plugin in BASELINE_MISSING_INIT:
        if (APP_PLUGINS / plugin / "__init__.py").is_file():
            stale.append(f"{plugin}/__init__.py（已存在，应从 BASELINE_MISSING_INIT 删除）")
    for plugin, dirs in BASELINE_EXTRA_DIRS.items():
        for dirname in dirs:
            if not (APP_PLUGINS / plugin / dirname).is_dir():
                stale.append(f"{plugin}/{dirname}/（已不存在，应从 BASELINE_EXTRA_DIRS 删除）")
    for plugin, files in BASELINE_ABSOLUTE_IMPORTS.items():
        for filename in files:
            if not (APP_PLUGINS / plugin / filename).is_file():
                stale.append(f"{plugin}/{filename}（已不存在，应从 BASELINE_ABSOLUTE_IMPORTS 删除）")
    assert not stale, "这些棘轮基线已过期，请删除：\n  " + "\n  ".join(stale)
