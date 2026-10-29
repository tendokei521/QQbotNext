"""闸三：目录可维护性（只保留能明确报错的硬规则）。

按 `docs/TEST_POLICY.md §5`，本文件已删除「目录/文件数/行数」清单快照型断言
（app 顶层白名单、agent 子包文件数上限、源文件行数闸门）——它们随重构必红，
却不提供回归价值；需要时改为 `scripts/*.py --check` 自动检查更好。

保留的规则：

1. **每个包必须有 `README.md`，且第一行是 `> 本文只负责：…`**——一句话说不清
   这个包负责什么，说明它该拆了；
2. 业务插件目录必须至少有一个 `main.py`（框架靠它作为加载入口）；
3. `tests/` 按源码镜像分区：根上不许堆 `test_*.py`，子目录要对应源码包；
4. 不允许只剩 `__pycache__` 的空壳目录。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# app/ 顶层允许的包目录（不含 __pycache__）。
ALLOWED_APP_PACKAGES = {
    "core",  # 内核：无业务依赖
    "domain",  # 领域模型与契约
    "infrastructure",  # 唯一 IO 出口
    "agent",  # 框架级 Agent（原 app/llm）
    "plugin",  # 插件**框架**（单数；原 app/modules）
    "plugins",  # **业务插件**（13 个，原 module/modules）
    "application",  # 应用服务（用例层；原 app/services）
    "webui",  # HTTP/WS 适配层
}

# README 首行的固定前缀。改这个前缀要同步改所有 README。
README_HEADER = "> 本文只负责："


def _packages(base: Path) -> list[Path]:
    return sorted(p for p in base.iterdir() if p.is_dir() and p.name != "__pycache__")


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


def test_business_plugins_have_module_entry() -> None:
    """每个业务插件目录必须至少有 main.py（框架靠它作为加载入口）。"""
    plugins_dir = ROOT / "app" / "plugins"
    if not plugins_dir.is_dir():
        return
    missing = [
        p.name
        for p in plugins_dir.iterdir()
        if p.is_dir() and p.name != "__pycache__" and not (p / "main.py").is_file()
    ]
    assert not missing, f"这些插件目录缺 main.py: {missing}"


def test_tests_are_partitioned_by_source_layer() -> None:
    """`tests/` 必须按源码镜像分区：`tests/test_*.py` 不允许堆在根上。

    「改哪个目录 → 跑哪些测试」应当由路径直接推出：
    `app/agent/loop/chat.py` ↔ `tests/agent/...`，`app/webui/api/x.py` ↔ `tests/webui/`。
    根上只允许放 `conftest.py`。
    """
    stray = sorted(p.name for p in (ROOT / "tests").glob("test_*.py"))
    assert not stray, (
        f"tests/ 根上还有 {len(stray)} 个测试文件没归位（应按源码镜像放进子目录）:\n  "
        + "\n  ".join(stray[:20])
    )


def test_tests_subdirs_mirror_source_packages() -> None:
    """`tests/<子目录>` 必须对应一个源码包；`tests/agent/<子目录>` 还要对应 agent 子包。"""
    # 顶层：app/ 的包 + 业务插件 + architecture（跨层静态检查，无对应源码包）
    allowed = set(ALLOWED_APP_PACKAGES) | {
        "plugins",
        "architecture",
        "eval",  # 跨包黑盒评测集
        "scripts",  # 仓库脚本（构建/生成工具，不在 app/ 下）
    }
    stray = sorted(
        p.name
        for p in (ROOT / "tests").iterdir()
        if p.is_dir() and p.name != "__pycache__" and p.name not in allowed
    )
    assert not stray, f"tests/ 下有不对应任何源码包的目录: {stray}（允许: {sorted(allowed)}）"

    # 第二层：tests/agent/* 必须对应 app/agent/* 的子包
    agent_src = {p.name for p in (ROOT / "app" / "agent").iterdir() if p.is_dir() and p.name != "__pycache__"}
    agent_tests_dir = ROOT / "tests" / "agent"
    stray2 = sorted(
        p.name
        for p in agent_tests_dir.iterdir()
        if p.is_dir() and p.name != "__pycache__" and p.name not in agent_src
    )
    assert not stray2, (
        f"tests/agent/ 下有不对应 app/agent/ 子包的目录: {stray2}（允许: {sorted(agent_src)}）"
    )


def test_no_stale_pycache_only_directories() -> None:
    """不允许只有 __pycache__、没有任何源文件的「空壳目录」（app/client 曾是这样）。

    跳过前端目录：`app/webui/dashboard/` 下的 node_modules 有自己的空目录语义
    （pnpm 的 `.pnpm`、缓存占位），不属于 Python 侧的结构问题。
    """
    stale: list[str] = []
    for path in (ROOT / "app").rglob("*"):
        if not path.is_dir() or path.name == "__pycache__":
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("app/webui/dashboard") or "node_modules" in rel:
            continue
        entries = [e for e in path.iterdir() if e.name != "__pycache__"]
        if not entries:
            stale.append(rel)
    assert not stale, f"这些目录只剩 __pycache__，应删除: {stale}"

