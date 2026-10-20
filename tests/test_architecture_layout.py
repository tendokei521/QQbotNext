"""闸三：目录完整性。

`docs/ARCHITECTURE.md §7.3` 说的「一眼看出每个文件夹负责什么」，靠这份测试变成
CI 能拦的东西，而不是靠自觉。

四条规则：

1. `app/` 顶层目录只允许白名单里的那些——新增目录必须先想清楚它属于哪一层，
   而不是顺手 `mkdir`；
2. **每个包必须有 `README.md`，且第一行是 `> 本文只负责：…`**——一句话说不清
   这个包负责什么，说明它该拆了；
3. `app/agent/` 每个子包文件数 ≤ 12——超过说明它又长成了一个平铺包；
4. 业务插件目录必须至少有一个 `module.py`。

这些是**棘轮**：白名单和上限只能收紧，不能放宽。真需要放宽时，改这里并写清理由。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# app/ 顶层允许的包目录（不含 __pycache__）。P5 计划把 modules→plugin、services→application。
ALLOWED_APP_PACKAGES = {
    "core",  # 内核：无业务依赖
    "domain",  # 领域模型与契约
    "infrastructure",  # 唯一 IO 出口
    "agent",  # 框架级 Agent（原 app/llm）
    "modules",  # 插件框架（P5 → plugin）
    "nodes",  # 消息节点链
    "services",  # 应用服务（P5 → application）
    "webui",  # HTTP/WS 适配层
}

# README 首行的固定前缀。改这个前缀要同步改所有 README。
README_HEADER = "> 本文只负责："

# 每个 agent 子包允许的最大 .py 文件数（不含 __init__.py 与 README）。
AGENT_PKG_MAX_FILES = 12


def _py_files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.glob("*.py") if p.name != "__init__.py")


def _packages(base: Path) -> list[Path]:
    return sorted(p for p in base.iterdir() if p.is_dir() and p.name != "__pycache__")


def test_app_top_level_packages_are_allowlisted() -> None:
    """app/ 顶层目录只能是白名单里的那些。"""
    actual = {p.name for p in _packages(ROOT / "app")}
    unexpected = actual - ALLOWED_APP_PACKAGES
    assert not unexpected, (
        f"app/ 下出现未登记的目录: {sorted(unexpected)}\n"
        "先决定它属于哪一层（core / domain / infrastructure / agent / plugin / "
        "application / webui），再更新 ALLOWED_APP_PACKAGES。"
    )
    missing = ALLOWED_APP_PACKAGES - actual
    assert not missing, f"白名单里的目录不存在了: {sorted(missing)}（删了目录请同步本测试）"


def test_every_app_package_has_responsibility_readme() -> None:
    """app/ 每个包都要有 README.md，首行是「本文只负责：…」。"""
    problems: list[str] = []
    for pkg in _packages(ROOT / "app"):
        readme = pkg / "README.md"
        if not readme.is_file():
            problems.append(f"{pkg.relative_to(ROOT).as_posix()}: 缺 README.md")
            continue
        first = readme.read_text(encoding="utf-8").lstrip().splitlines()[0]
        if not first.startswith(README_HEADER):
            problems.append(f"{pkg.relative_to(ROOT).as_posix()}/README.md: 首行不是 {README_HEADER}…（实际: {first[:40]}）")
    assert not problems, "包职责说明不合规:\n  " + "\n  ".join(problems)


def test_every_agent_subpackage_has_responsibility_readme() -> None:
    """app/agent/ 的每个子包也要有 README.md（子包多，最容易忘记写）。"""
    problems: list[str] = []
    for pkg in _packages(ROOT / "app" / "agent"):
        readme = pkg / "README.md"
        if not readme.is_file():
            problems.append(f"agent/{pkg.name}: 缺 README.md")
            continue
        first = readme.read_text(encoding="utf-8").lstrip().splitlines()[0]
        if not first.startswith(README_HEADER):
            problems.append(f"agent/{pkg.name}/README.md: 首行不是 {README_HEADER}…")
    assert not problems, "agent 子包职责说明不合规:\n  " + "\n  ".join(problems)


def test_agent_subpackages_stay_small() -> None:
    """agent 子包的 .py 文件数 ≤ 12（超了说明又该拆了）。"""
    oversized = {
        pkg.name: len(_py_files(pkg))
        for pkg in _packages(ROOT / "app" / "agent")
        if len(_py_files(pkg)) > AGENT_PKG_MAX_FILES
    }
    assert not oversized, f"这些 agent 子包文件过多（> {AGENT_PKG_MAX_FILES}）: {oversized}"


def test_agent_top_level_stays_flat_but_small() -> None:
    """app/agent/ 顶层只放「跨子包共享的门面」，不能再次堆积成平铺包。"""
    top = _py_files(ROOT / "app" / "agent")
    assert len(top) <= 10, (
        f"app/agent/ 顶层已有 {len(top)} 个文件（上限 10）: {[p.name for p in top]}\n"
        "新功能请先放进对应的子包；只有真正跨子包共享的门面才留在顶层。"
    )


def test_business_plugins_have_module_entry() -> None:
    """每个业务插件目录必须至少有 module.py（框架靠它作为加载入口）。"""
    plugins_dir = ROOT / "module" / "modules"
    if not plugins_dir.is_dir():
        return
    missing = [
        p.name
        for p in plugins_dir.iterdir()
        if p.is_dir() and p.name != "__pycache__" and not (p / "module.py").is_file()
    ]
    assert not missing, f"这些插件目录缺 module.py: {missing}"


def test_no_stale_pycache_only_directories() -> None:
    """不允许只有 __pycache__、没有任何源文件的「空壳目录」（app/client 曾是这样）。"""
    stale: list[str] = []
    for path in (ROOT / "app").rglob("*"):
        if not path.is_dir() or path.name == "__pycache__":
            continue
        entries = [e for e in path.iterdir() if e.name != "__pycache__"]
        if not entries:
            stale.append(path.relative_to(ROOT).as_posix())
    assert not stale, f"这些目录只剩 __pycache__，应删除: {stale}"
