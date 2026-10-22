"""运行时属性契约：`getattr(runtime, "X")` 里的 X 必须真实存在或显式白名单化。

## 为什么需要

v3 把记忆/知识库搬进模块后，`AgentRuntime` 少了一批字段，但代码里
`getattr(runtime, "memory", None)` 这种写法**不会报错**，只会静默返回 None ——
于是记忆工具、主动消息的记忆召回全部悄悄失效，测试还是绿的。

这条闸门把"隐式依赖"变成显式清单：新增/删除运行时字段时，
要么代码同步改掉，要么在这里登记原因。

## 判据

合法来源（任一即算存在）：
1. `app/agent/runtime.py` 里 `self.<name> = ...`；
2. `AgentRuntime` 的 `@property` / 方法名；
3. `setattr(runtime, "<name>", ...)`（模块在 on_load 挂的句柄）；
4. `*_options` 策略视图槽（模块挂载的配置视图，见 `app/agent/policy.py`）；
5. `_LEGACY_FALLBACK` 白名单（兼容测试替身，需要写清原因）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"
RUNTIME_PY = APP / "agent" / "runtime.py"

#: 模块运行时挂载的句柄（`setattr(runtime, ...)` 之外的白名单，含原因）
_DOCUMENTED_HANDLES = {
    # v3：业务模块把自己实现的服务挂到这些句柄上，框架按鸭子类型使用
    "memory_service": "llm_memory.on_load 挂载的记忆管理器（框架已无 memory 字段）",
    "knowledge_service": "llm_knowledge.on_load 挂载的知识库管理器（框架已无 knowledge 字段）",
    # 设计上就允许为 None 的槽
    "output": "输出策略槽（llm_output 接管；None = 框架默认）",
    "assembler": "组装器槽（llm_assembly 接管）",
    "invoker": "provider 调用策略槽（provider_invoker 接管）",
    "llm_tools": "用户自定义工具注册表（可为 None）",
    "skills": "技能注册表（可为 None）",
    "telemetry": "遥测记录器（可为 None）",
    "group_log": "群聊记录器（可为 None）",
    "bot": "当前连接（未注入时为 None）",
}

#: 仅用于兼容测试替身的旧字段读取（生产路径必须走新句柄）
_LEGACY_FALLBACK = {
    "memory": "旧字段，v3 已删；仅 `_resolve_memory()` 的测试替身回退分支会读",
    "knowledge": "旧字段，v3 已删；仅 `_kb()` 的测试替身回退分支会读",
    "mcp_manager": "旧字段，v3 已删；仅历史代码兼容",
    "session_mgr": "真实字段，但旧代码用 getattr 读（保留）",
}

#: `*_options` 策略视图槽（由 policy.py 统一定义）
_OPTION_SLOTS = {
    "assembly_options",
    "meta_options",
    "context_options",
    "memory_options",
    "knowledge_options",
    "tools_options",
    "output_options",
    "proactive_options",
    "schedule_options",
}


def _runtime_attrs() -> set[str]:
    """从 `AgentRuntime` 源码里收集"真实存在"的属性名。"""
    tree = ast.parse(RUNTIME_PY.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        # self.x = ...
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    names.add(target.attr)
        # self.x: T = ...
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                names.add(target.attr)
        # def f / @property
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
    return names


def _setattr_handles() -> set[str]:
    """全仓 `setattr(<x>, "attr", ...)` 声明的运行时句柄。

    覆盖两种写法：
    - 模块侧 `setattr(runtime, "memory_service", ...)`；
    - 框架侧 `setattr(self, "interrupt_save_sent", ...)`（`AgentRuntime` 的方法里写）。
    """
    names: set[str] = set()
    for path in APP.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        # setattr(runtime, "x", ...)
        names.update(re.findall(r'setattr\(\s*(?:self\.)?runtime,\s*"(\w+)"', text))
        # 直接赋值：runtime.x = ... / ctx.runtime.x = ... / self.runtime.x = ...
        names.update(re.findall(r'\bruntime\.(\w+)\s*=\s*[^=]', text))
        # AgentRuntime 自己的方法里 setattr(self, "x", ...)
        if path.parent == RUNTIME_PY.parent:
            names.update(re.findall(r'setattr\(\s*self,\s*"(\w+)"', text))
    return names


def _runtime_getattr_reads() -> dict[str, list[str]]:
    """收集全仓 `getattr(<...runtime...>, "x", ...)` 读取的属性。"""
    reads: dict[str, list[str]] = {}
    for path in APP.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if path == RUNTIME_PY:
            continue
        rel = path.relative_to(ROOT).as_posix()
        for i, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            for m in re.finditer(r'getattr\(\s*([\w\.]+),\s*"(\w+)"', line):
                expr, attr = m.group(1), m.group(2)
                # 只认"运行时"表达式：runtime / self.runtime / ctx.runtime / tool_ctx.runtime …
                if not re.fullmatch(r"(\w+\.)*runtime", expr):
                    continue
                reads.setdefault(attr, []).append(f"{rel}:{i}")
    return reads


def test_runtime_getattr_reads_resolve_to_real_attributes():
    """每处 `getattr(runtime, "X")` 的 X 都必须是真实字段、文档化句柄或白名单项。"""
    known = _runtime_attrs() | _setattr_handles() | _OPTION_SLOTS
    known |= set(_DOCUMENTED_HANDLES) | set(_LEGACY_FALLBACK)

    reads = _runtime_getattr_reads()
    unknown = {
        attr: where for attr, where in reads.items() if attr not in known
    }
    assert not unknown, (
        "这些 getattr(runtime, ...) 读的属性既不在 AgentRuntime 上、也没登记白名单：\n"
        + "\n".join(f"  {attr}: {where[:3]}" for attr, where in sorted(unknown.items()))
    )


def test_documented_handles_are_still_used():
    """白名单不许留下"已经没人用"的条目（否则清单会烂掉）。"""
    reads = _runtime_getattr_reads()
    stale = [name for name in _DOCUMENTED_HANDLES if name not in reads]
    assert not stale, f"这些文档化句柄已无人读取，请从清单移除: {stale}"


def test_legacy_fallbacks_are_documented_and_few():
    """旧字段回退必须显式登记，且总量受控（防止 v3 的静默失效再次扩散）。"""
    assert len(_LEGACY_FALLBACK) <= 4, (
        f"旧字段回退项涨到 {len(_LEGACY_FALLBACK)} 个，先考虑改代码而不是加白名单"
    )
    for name, reason in _LEGACY_FALLBACK.items():
        assert reason and len(reason) > 6, f"{name} 必须写清为什么还能读"


def test_no_new_runtime_memory_or_knowledge_reads_in_framework():
    """框架侧（app/agent）不得再直接读 `runtime.memory` / `runtime.knowledge`。"""
    offenders = []
    for path in (APP / "agent").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(ROOT).as_posix()
        for i, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if re.search(r'getattr\(\s*(?:[\w]+\.)*runtime,\s*"(memory|knowledge)"', line):
                offenders.append(f"{rel}:{i}")
    assert not offenders, f"框架仍在读已删除的运行时字段: {offenders}"
