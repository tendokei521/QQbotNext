"""架构边界测试（对应 `docs/ARCHITECTURE.md §7 闸二` 与 `STYLE.md §12`）。

这不是风格检查，而是把架构决策变成 CI 能拦的红灯。它回答一个问题：
**「这条依赖方向是被允许的吗？」**——而不是靠人记得。

## 六条规则

1. `domain/` 只准依赖 `core/` 与自身（纯规则层，可单测）；
2. `infrastructure/` 不准依赖上层（agent / plugin / application / webui）；
3. 生产代码不准 `from app.bootstrap import ...`（服务定位器的来源）；
4. `get_container()` 直调是**存量债**，只许降不许升；
5. `webui/` 不准直接 import agent 内部实现（应经 `application/` 门面）；
6. `app/llm/__init__.py` 必须保持薄壳——不准重导出 Agent 主循环。

## 预算制（棘轮）

规则 3、4、5 有历史欠账，无法一次清零，所以用 `_BUDGET` 里的数字做棘轮：
**修好一处就把预算调小一处**；调大必须在提交说明里写清理由。

预算只统计"真实代码"，用 AST 解析——注释与文档字符串里提到 `get_container()`
不算（否则本文件自己也会违规）。

## 相关

- 闭包与环的度量脚本：`scripts/gen/arch_closure.py --assert-max 8000`
- 现状描述：`STYLE.md §12`；目标结构：`docs/ARCHITECTURE.md §2.2`
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

AGENT_PREFIXES = ("app.agent",)  # P2 后 agent 层只有这一个前缀（app.llm 已并入）

# 存量债预算：只许降，不许升。修好一处就把对应数字改小。
_BUDGET = {
    # P3 已清零：生产代码不再从装配根取容器（改用 app.core.container.get_active）
    "bootstrap_imports": 0,
    # 剩下的都在 app/webui/api/*。`deps.py` 已经给每个服务备了具名依赖
    # （get_bot_service 等），把端点改成 `Depends(get_xxx_service)` 就能继续降。
    # 已清零：logs.py（4 端点，3 条 HTTP 用例守住）、framework.py、resources.py、
    # webui_cfg.py（12 端点）、bots.py（10 端点）。剩下 agent.py / modules.py 两个大文件。
    "get_container_calls": 39,
    # 端点里自己 `container.get(类型)` 的次数（服务定位的味道）。目标 0。
    "webui_container_get_calls": 32,
    # app/webui/api/{agent,media,modules,prompt_dump}.py 直接 import app.agent.*
    "webui_to_agent_imports": 13,
}

# `app/agent/__init__.py` 允许在模块层 import 的东西（全部廉价、无重依赖）
_ALLOWED_AGENT_PKG_IMPORTS = {
    "app.core.logger",
    "app.infrastructure.fs.paths",
    "app.agent.hooks",
    "app.agent.loop.context",
    "app.agent.persona.skills",
    "app.agent.tools.spec",
}


# --------------------------------------------------------------------------- #
# 扫描工具
# --------------------------------------------------------------------------- #
def _iter_py(*subdirs: str):
    """遍历仓库内指定子目录下的所有 .py（跳过 __pycache__）。"""
    for sub in subdirs:
        base = REPO_ROOT / sub
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            yield path


def _parse(path: Path) -> ast.Module | None:
    # utf-8-sig：仓库里有几个文件带 BOM，用 utf-8 解出来的 \ufeff 会让 ast 报错，
    # 导致这些文件被静默跳过（曾因此漏查）。
    try:
        return ast.parse(path.read_text(encoding="utf-8-sig"))
    except (SyntaxError, OSError):  # pragma: no cover - 语法错误由别的测试负责
        return None


def _import_targets(path: Path, *, module_level_only: bool) -> list[str]:
    """返回绝对 import 的模块名列表（相对 import 不跨层，忽略）。"""
    tree = _parse(path)
    if tree is None:
        return []
    targets: list[str] = []
    stack: list[ast.AST] = [tree]
    while stack:
        node = stack.pop()
        if module_level_only and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        stack.extend(ast.iter_child_nodes(node))
        if isinstance(node, ast.ImportFrom):
            if not node.level and node.module:
                targets.append(node.module)
        elif isinstance(node, ast.Import):
            targets.extend(alias.name for alias in node.names)
    return targets


def _count_get_container_calls(path: Path) -> int:
    """只数真实的 `get_container(...)` 调用，不算注释/文档字符串。"""
    tree = _parse(path)
    if tree is None:
        return 0
    total = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Name) and fn.id == "get_container":
            total += 1
        elif isinstance(fn, ast.Attribute) and fn.attr == "get_container":
            total += 1
    return total


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _report(hits: list[tuple[str, str]], budget_key: str, hint: str) -> None:
    """命中数必须 ≤ 预算；打印可操作的失败信息。"""
    budget = _BUDGET[budget_key]
    if len(hits) <= budget:
        return
    detail = "\n".join(f"    {where}  ->  {what}" for where, what in sorted(hits)[:40])
    pytest.fail(
        f"边界违规 {len(hits)} 处，超过预算 {budget}（{budget_key}）。\n"
        f"{detail}\n"
        f"处理方式：{hint}\n"
        f"若确实无法避免，请在提交说明里写明理由并上调 _BUDGET['{budget_key}']。"
    )


# --------------------------------------------------------------------------- #
# 规则 1：domain 纯规则层
# --------------------------------------------------------------------------- #
def test_domain_depends_only_on_core_and_itself():
    """`domain/` 只能 import `app.core.*` 与 `app.domain.*`——否则它就不是纯规则层了。"""
    offenders: list[tuple[str, str]] = []
    for path in _iter_py("app/domain"):
        for target in _import_targets(path, module_level_only=False):
            if not target.startswith("app."):
                continue
            if target == "app.domain" or target.startswith("app.domain."):
                continue
            if target == "app.core" or target.startswith("app.core."):
                continue
            offenders.append((_rel(path), target))
    if offenders:
        detail = "\n".join(f"    {f}  ->  {m}" for f, m in sorted(offenders))
        pytest.fail(
            "domain/ 出现了越界依赖（domain 必须无 IO、无网络、无 DB，可纯单测）：\n"
            f"{detail}\n"
            "处理方式：把越界的部分留在 agent/ 或 infrastructure/，只把纯规则搬进 domain/。"
        )


# --------------------------------------------------------------------------- #
# 规则 2：infrastructure 不依赖上层
# --------------------------------------------------------------------------- #
def test_infrastructure_does_not_import_upper_layers():
    """`infrastructure/` 是最底层，只准 import core / domain / 自身。"""
    upper = ("app.agent", "app.plugin", "app.application", "app.webui", "app.services", "app.plugin")
    offenders: list[tuple[str, str]] = []
    for path in _iter_py("app/infrastructure"):
        for target in _import_targets(path, module_level_only=False):
            if target.startswith(AGENT_PREFIXES) or target.startswith(upper):
                offenders.append((_rel(path), target))
    if offenders:
        detail = "\n".join(f"    {f}  ->  {m}" for f, m in sorted(offenders))
        pytest.fail(
            "infrastructure/ 反向依赖了上层：\n"
            f"{detail}\n"
            "处理方式：把需要的抽象（ABC / 数据结构）放到 domain/，由上层实现并注册进来。"
        )


# --------------------------------------------------------------------------- #
# 规则 3：生产代码不准 from app.bootstrap import
# --------------------------------------------------------------------------- #
def test_no_production_imports_from_bootstrap():
    """`app.bootstrap` 是装配根，谁都不准依赖它——否则装配顺序就成了隐式契约。"""
    offenders: list[tuple[str, str]] = []
    for path in _iter_py("app", "module"):
        for target in _import_targets(path, module_level_only=False):
            if target == "app.bootstrap":
                offenders.append((_rel(path), "from app.bootstrap import ..."))
    _report(
        offenders,
        "bootstrap_imports",
        "改成构造函数注入，或 FastAPI Depends（app/webui/api/deps.py）。",
    )


# --------------------------------------------------------------------------- #
# 规则 4：get_container() 直调只许降
# --------------------------------------------------------------------------- #
def test_get_container_calls_within_budget():
    """服务定位器是债：每调一次，这个文件就焊进了整个 Agent 实现。"""
    offenders: list[tuple[str, str]] = []
    for path in _iter_py("app", "module"):
        n = _count_get_container_calls(path)
        if n:
            offenders.extend([(_rel(path), f"get_container() ×{n}")] * n)
    _report(
        offenders,
        "get_container_calls",
        "把依赖改成函数参数或 FastAPI Depends；webui/api/deps.py 是落点。",
    )


def _count_container_get_calls(path: Path) -> int:
    """数 `container.get(...)`：端点自己从容器里按类型捞服务。"""
    tree = _parse(path)
    if tree is None:
        return 0
    total = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if (
            isinstance(fn, ast.Attribute)
            and fn.attr == "get"
            and isinstance(fn.value, ast.Name)
            and fn.value.id == "container"
        ):
            total += 1
    return total


def test_webui_container_get_calls_within_budget():
    """端点不该自己 `container.get(类型)`：依赖应当出现在函数签名上。"""
    offenders: list[tuple[str, str]] = []
    for path in _iter_py("app/webui/api"):
        n = _count_container_get_calls(path)
        if n:
            offenders.extend([(_rel(path), f"container.get() ×{n}")] * n)
    _report(
        offenders,
        "webui_container_get_calls",
        "改成 `x = Depends(get_x_service)`（deps.py 已备好每个服务的具名依赖）。",
    )


# --------------------------------------------------------------------------- #
# 规则 5：webui 不碰 agent 内部
# --------------------------------------------------------------------------- #
def test_webui_does_not_import_agent_internals():
    """WebUI 是 HTTP 适配层，只该认识 `application/` 的门面。"""
    offenders: list[tuple[str, str]] = []
    for path in _iter_py("app/webui"):
        for target in _import_targets(path, module_level_only=False):
            if target.startswith(AGENT_PREFIXES):
                offenders.append((_rel(path), target))
    _report(
        offenders,
        "webui_to_agent_imports",
        "在 application/ 加一个门面函数，webui 只调门面。",
    )


# --------------------------------------------------------------------------- #
# 规则 6：app/agent/__init__.py 必须是薄壳
# --------------------------------------------------------------------------- #
def test_agent_package_init_does_not_import_the_loop():
    """包 `__init__` 不准成为通往主循环的后门。

    历史教训：`app/llm/__init__.py`（P2 后即 `app/agent/__init__.py`）曾急切
    import `chat`，于是全库 121 处 `from app.llm import logger` 都被拖去加载主
    循环，把每个入口的可达闭包放大到 152 文件 / 46,243 行。这条测试守住那个修复。
    """
    init_path = REPO_ROOT / "app" / "agent" / "__init__.py"
    assert init_path.exists(), "app/agent/__init__.py 不见了？"
    offending = [
        target
        for target in _import_targets(init_path, module_level_only=False)
        if target.startswith(AGENT_PREFIXES) and target not in _ALLOWED_AGENT_PKG_IMPORTS
    ]
    # 相对 import（from .loop.chat import handle）不在上面列表里，单独抓
    tree = _parse(init_path)
    assert tree is not None
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level and node.module:
            absolute = f"app.agent.{node.module}"
            if absolute not in _ALLOWED_AGENT_PKG_IMPORTS:
                offending.append(f".{node.module}")
    assert not offending, (
        f"app/agent/__init__.py 重新 import 了重模块：{sorted(set(offending))}。\n"
        f"允许的只有 {sorted(_ALLOWED_AGENT_PKG_IMPORTS)}。\n"
        "需要 handle 的地方请直接 `from app.agent.loop.chat import handle`——"
        "包级重导出会在 import 图上形成 app.agent ⇄ app.agent.loop.chat 的环。"
    )


def test_agent_package_public_symbols_are_objects_not_modules():
    """`from app.agent import tool` 必须拿到函数，而不是同名的 `app.agent.tools` 模块。

    这是 PEP 562 `__getattr__` 的经典陷阱：子模块一旦被 import 就成为包属性，
    同名符号的 `__getattr__` 永远轮不到。曾因此静默返回模块对象。
    """
    import types

    import app.agent as pkg

    for name in pkg.__all__:
        value = getattr(pkg, name)
        assert not isinstance(value, types.ModuleType), (
            f"app.agent.{name} 解析成了模块对象（{value!r}），应当是其内部的真实符号。"
        )


def test_old_llm_package_is_gone():
    """`app/llm/` 不得复活——一个概念只能有一个名字。

    P2 把 91 个文件全部并入 `app/agent/`。若有人再建 `app/llm/`（哪怕是重导出
    门面），`from app.llm import ...` 与 `from app.agent import ...` 就会成为两套
    并存的入口，职责又变模糊，且门面的 import 会在依赖图上重新制造 app 层内部环。
    """
    assert not (REPO_ROOT / "app" / "llm").exists(), (
        "app/llm/ 又出现了。实现只在 app/agent/，请直接 import app.agent.*"
    )
