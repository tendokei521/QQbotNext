"""闸三：目录完整性。

`docs/ARCHITECTURE.md §7.3` 说的「一眼看出每个文件夹负责什么」，靠这份测试变成
CI 能拦的东西，而不是靠自觉。

四条规则：

1. `app/` 顶层目录只允许白名单里的那些——新增目录必须先想清楚它属于哪一层，
   而不是顺手 `mkdir`；
2. **每个包必须有 `README.md`，且第一行是 `> 本文只负责：…`**——一句话说不清
   这个包负责什么，说明它该拆了；
3. `app/agent/` 每个子包文件数 ≤ 12——超过说明它又长成了一个平铺包；
4. 业务插件目录必须至少有一个 `main.py`。

这些是**棘轮**：白名单和上限只能收紧，不能放宽。真需要放宽时，改这里并写清理由。
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

# 每个 agent 子包允许的最大 .py 文件数（不含 __init__.py 与 README）。
# 15 → 17（v3）：`loop/turn.py` 是「无事件的完整一轮对话」的**公开入口**
# （`run_turn` / `TurnRequest` / `TurnResult`），模块侧 `llm_turn` 直接调它；
# `loop/providers.py` 是「上下文块 / 工具源」注册表的契约与收集实现。
# 两者都是 loop 层的编排职责，不该塞进 generate/chat/prompt_prep 任一个里。
# 17 → 18：`loop/tool_step.py` 是工具循环**唯一**的循环体（P1 把 6 处收成 1 处），
# 属于 loop 层的编排职责；它取代的是散落在 chat/turn/proactive/provider 里的副本。
# 18 → 19：`loop/turn_runner.py` 放一轮对话的**结构化结局**（TurnReason）与状态机，
# 后续的 AbortScope 也在同一文件里长大（不再新增文件）。它属于编排层，不是业务。
AGENT_PKG_MAX_FILES = 20


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


# --------------------------------------------------------------------------- #
# 源文件长度：只许降的棘轮
# --------------------------------------------------------------------------- #
#: 允许超长的文件（豁免名单只能缩小）。每修完一个文件就把它从这里删掉。
_LONG_FILE_ALLOWLIST = {
    "app/agent/config_schema.py": 1260,  # 数据 + _gate/_on DSL + v3 收敛段 + P3/P5 工具执行键，刻意不拆
    # v3：新增 `build_extra_blocks` 块（模块贡献的额外 system 块）与其 docstring，
    # 809 行。装配器是"块表 + 12 个块函数"的单一职责文件，拆开反而要跨文件读顺序；
    # 收紧方式：等 W3 把 behavior/emoji/meta 三块迁到 `llm_assembly` 模块后自然回落 <800。
    # 提示词方案（prompt_preset）：块 2~6 的正文改由 `assembly.presets` 提供，装配器只多出
    # "选中方案则走方案"的分支（约 +35 行），文案本体全在新文件里，故不拆本文件。
    "app/agent/assembly/assembler.py": 880,
}
MAX_FILE_LINES = 800


def test_no_file_grows_beyond_allowlist() -> None:
    """`app/**/*.py` 单个文件 ≤ 800 行；豁免名单里的文件只许变短。

    `chat.py` 曾是 2,192 行——改一句话要在一屏外的上下文里找。这条闸把「又长回去了」
    变成 CI 会红的事，而不是靠记性。
    """
    offenders: list[str] = []
    for path in (ROOT / "app").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if "__pycache__" in rel or "dashboard" in rel:
            continue
        lines = path.read_bytes().count(b"\n")
        limit = _LONG_FILE_ALLOWLIST.get(rel, MAX_FILE_LINES)
        if lines > limit:
            offenders.append(f"{rel}: {lines} 行 > 上限 {limit}")
    assert not offenders, "这些文件超长（拆完记得同步缩小豁免名单）:\n  " + "\n  ".join(offenders)
