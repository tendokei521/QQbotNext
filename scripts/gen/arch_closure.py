#!/usr/bin/env python
"""架构闸一：import 图可达闭包与强连通块（SCC）度量。

## 这个脚本解决什么问题

`app/agent/loop/chat.py` 改一行，静态可达 143 个文件 / 42,082 行——也就是说「改一个文件
要读多少个文件」这件事，长期只能靠感觉。本脚本把它变成**可测量、可设阈值**的数字。

## 口径（必须与 STYLE.md §12 一致）

- **行数** = 文件总行数（``text.count("\\n")``），含空行与注释。不用「非空行」。
- **依赖边** = 静态 import，包含：
  - 顶层 ``import a.b`` / ``from a import b``
  - **函数内惰性 import**（用 ``ast.walk`` 覆盖，这是历史债的主要藏身地）
  - 相对 import（``from .x import y``）
- **闭包** = 入口文件 + 从它出发前向可达的全部模块（不含标准库/三方）。

## 用法

    python scripts/gen/arch_closure.py                    # 总览：SCC 排名 + 默认入口闭包
    python scripts/gen/arch_closure.py --scc              # 只列强连通块
    python scripts/gen/arch_closure.py --entry app.agent.loop.chat --entry app.webui.api.agent
    python scripts/gen/arch_closure.py --json out.json    # 机器可读
    python scripts/gen/arch_closure.py --assert-max 8000  # 任一入口闭包超阈值则退出码 1

## 为什么不用 pydeps / import-linter

它们只看顶层 import，看不见全库 315 处函数内惰性 import——而环正是靠惰性 import 形成的。
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# 扫描这些顶层包（相对仓库根）
SCAN_ROOTS = ("app", "module", "scripts")

# 默认入口：改这些文件时你最想知道"会牵动多少代码"
DEFAULT_ENTRIES = (
    "app.bootstrap",
    "app.agent.loop.chat",
    "app.agent.assembly.assembler",
    "app.agent.providers",
    "app.agent.proactivity.scheduler",
    "app.agent.memory",
    "app.agent.knowledge",
    "app.webui.app",
    "app.webui.api.agent",
    "app.webui.api.modules",
    "app.plugin.registry",
    "app.infrastructure.onebot.gateway",
)


# --------------------------------------------------------------------------- #
# 模块发现
# --------------------------------------------------------------------------- #
def module_name_for(path: Path) -> str:
    """``app/llm/chat.py`` -> ``app.agent.loop.chat``；``app/llm/__init__.py`` -> ``app.agent``。"""
    rel = path.relative_to(REPO_ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def discover_modules() -> dict[str, Path]:
    """扫描 SCAN_ROOTS 下所有 .py，返回 {模块名: 路径}。"""
    found: dict[str, Path] = {}
    for root_name in SCAN_ROOTS:
        root = REPO_ROOT / root_name
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for fname in filenames:
                if not fname.endswith(".py"):
                    continue
                path = Path(dirpath) / fname
                found[module_name_for(path)] = path
    return found


def line_count(path: Path) -> int:
    """总行数（含空行），与 STYLE.md §12 口径一致。"""
    try:
        return path.read_text(encoding="utf-8", errors="replace").count("\n")
    except OSError:
        return 0


# --------------------------------------------------------------------------- #
# import 抽取
# --------------------------------------------------------------------------- #
def extract_imports(
    path: Path, modname: str, modules: dict[str, Path], include_lazy: bool = True
) -> set[str]:
    """抽取一个文件里**所有**内部依赖（含函数内惰性 import）。

    返回模块名集合；``from app.x import SomeClass`` 这类候选会被逐个尝试解析，
    只保留真实存在的模块。

    ``include_lazy=False`` 时忽略函数体内的 import——用来回答
    「这个环是惰性 import 造出来的，还是顶层就有的」。
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set()

    # 当前文件所属包，用于解析相对 import
    pkg_parts = modname.split(".") if path.name == "__init__.py" else modname.split(".")[:-1]
    deps: set[str] = set()

    def add_candidate(name: str) -> None:
        """把 ``a.b.c`` 从长到短尝试为已知模块；命中即收下并停止。"""
        if not name:
            return
        parts = name.split(".")
        for i in range(len(parts), 0, -1):
            cand = ".".join(parts[:i])
            if cand in modules:
                if cand != modname:
                    deps.add(cand)
                return

    def relevant_nodes(node: ast.AST):
        """遍历节点；include_lazy=False 时不进入函数体（用于诊断"环是否由惰性 import 造成"）。"""
        stack = [node]
        while stack:
            cur = stack.pop()
            if not include_lazy and isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            yield cur
            stack.extend(ast.iter_child_nodes(cur))

    for node in relevant_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                add_candidate(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # 相对 import：level=1 指当前包，level=2 再上一层
                keep = len(pkg_parts) - (node.level - 1)
                base = ".".join(pkg_parts[:keep]) if keep > 0 else ""
                target = f"{base}.{node.module}" if (base and node.module) else (node.module or base)
            else:
                target = node.module or ""
            if not target:
                continue
            add_candidate(target)
            # ``from pkg import sub`` —— sub 可能是个子模块
            for alias in node.names:
                if alias.name != "*":
                    add_candidate(f"{target}.{alias.name}")
    return deps


# --------------------------------------------------------------------------- #
# 图算法
# --------------------------------------------------------------------------- #
@dataclass
class Graph:
    modules: dict[str, Path]
    edges: dict[str, set[str]] = field(default_factory=dict)
    sizes: dict[str, int] = field(default_factory=dict)

    def forward_closure(self, start: str) -> set[str]:
        """返回从 start 前向可达的模块集合（含 start 自身）。"""
        visited = {start}
        queue = deque([start])
        while queue:
            cur = queue.popleft()
            for nxt in self.edges.get(cur, ()):
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append(nxt)
        return visited

    def closure_size(self, start: str) -> tuple[int, int]:
        """返回 (闭包文件数含自身, 闭包总行数含自身)。"""
        files = self.forward_closure(start)
        return len(files), sum(self.sizes.get(f, 0) for f in files)

    def strongly_connected(self) -> list[set[str]]:
        """迭代版 Tarjan，返回所有大小 > 1 的强连通块（按行数降序）。"""
        index: dict[str, int] = {}
        low: dict[str, int] = {}
        on_stack: set[str] = set()
        stack: list[str] = []
        counter = 0
        result: list[set[str]] = []

        for root in sorted(self.edges):
            if root in index:
                continue
            index[root] = low[root] = counter
            counter += 1
            stack.append(root)
            on_stack.add(root)
            work: list[tuple[str, list[str], int]] = [
                (root, sorted(n for n in self.edges.get(root, ()) if n in self.edges), 0)
            ]
            while work:
                node, neighbours, pos = work[-1]
                if pos < len(neighbours):
                    work[-1] = (node, neighbours, pos + 1)
                    nxt = neighbours[pos]
                    if nxt not in index:
                        index[nxt] = low[nxt] = counter
                        counter += 1
                        stack.append(nxt)
                        on_stack.add(nxt)
                        work.append(
                            (nxt, sorted(n for n in self.edges.get(nxt, ()) if n in self.edges), 0)
                        )
                    elif nxt in on_stack:
                        low[node] = min(low[node], index[nxt])
                    continue
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index[node]:
                    comp: set[str] = set()
                    while True:
                        member = stack.pop()
                        on_stack.discard(member)
                        comp.add(member)
                        if member == node:
                            break
                    if len(comp) > 1:
                        result.append(comp)

        result.sort(key=lambda c: -sum(self.sizes.get(m, 0) for m in c))
        return result

    def cycle_of(self, module: str) -> set[str] | None:
        for comp in self.strongly_connected():
            if module in comp:
                return comp
        return None


def build_graph(modules: dict[str, Path], include_lazy: bool = True) -> Graph:
    g = Graph(modules=modules)
    for modname, path in modules.items():
        g.edges[modname] = extract_imports(path, modname, modules, include_lazy=include_lazy)
        g.sizes[modname] = line_count(path)
    return g


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #
def _force_utf8_stdout() -> None:
    """Windows 控制台默认 GBK，打印非 ASCII 会 UnicodeEncodeError。CI 与本地统一 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover - 极端环境
                pass


def report_overview(g: Graph, entries: tuple[str, ...]) -> None:
    total_lines = sum(g.sizes.values())
    print(f"仓库根: {REPO_ROOT}")
    print(f"模块数: {len(g.modules)}   总行数: {total_lines:,}")
    print(f"依赖边数: {sum(len(v) for v in g.edges.values()):,}")

    comps = g.strongly_connected()
    in_cycle = sum(len(c) for c in comps)
    print(
        f"\n强连通块: {len(comps)} 个，涉及 {in_cycle} 个模块 / "
        f"{sum(g.sizes.get(m, 0) for c in comps for m in c):,} 行"
    )
    acyclic = len(g.modules) - in_cycle
    print(f"无环模块: {acyclic} 个（{acyclic / len(g.modules) * 100:.0f}%）")

    for i, comp in enumerate(comps[:3], 1):
        lines = sum(g.sizes.get(m, 0) for m in comp)
        print(f"\n  环 #{i}  {len(comp)} 文件 / {lines:,} 行")
        for m in sorted(comp, key=lambda x: -g.sizes.get(x, 0))[:40]:
            print(f"        {g.sizes.get(m, 0):>6,}  {m}")

    print("\n入口闭包（改这个文件会牵动多少代码）:")
    print(f"  {'入口':<44} {'文件':>6} {'行数':>10}  环内")
    for entry in entries:
        if entry not in g.edges:
            print(f"  {entry:<44} {'(不存在)':>6}")
            continue
        nf, nl = g.closure_size(entry)
        in_ring = "[环内]" if g.cycle_of(entry) else "      "
        print(f"  {entry:<44} {nf:>6} {nl:>10,}  {in_ring}")


def explain_cycle(g: Graph, module: str) -> list[str] | None:
    """找一条经过 module 的最短回路（module -> ... -> module），用于定位环是怎么闭上的。"""
    if module not in g.edges:
        return None
    # BFS：从 module 的邻居出发，找回到 module 的最短路径
    prev: dict[str, str] = {}
    queue = deque()
    for nxt in sorted(g.edges.get(module, ())):
        if nxt == module:
            return [module, module]
        if nxt not in prev:
            prev[nxt] = module
            queue.append(nxt)
    while queue:
        cur = queue.popleft()
        for nxt in sorted(g.edges.get(cur, ())):
            if nxt == module:
                path = [cur]
                while path[-1] != module:
                    path.append(prev[path[-1]])
                return [module, *reversed(path)]
            if nxt not in prev:
                prev[nxt] = cur
                queue.append(nxt)
    return None


def report_scc(g: Graph) -> None:
    for comp in g.strongly_connected():
        lines = sum(g.sizes.get(m, 0) for m in comp)
        print(f"\n=== {len(comp)} 文件 / {lines:,} 行 ===")
        for m in sorted(comp, key=lambda x: -g.sizes.get(x, 0)):
            print(f"  {g.sizes.get(m, 0):>6,}  {m}")


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()
    ap = argparse.ArgumentParser(description="import 图闭包与强连通块度量")
    ap.add_argument(
        "--entry", action="append", metavar="MODULE", help="入口模块名，可重复；缺省用内置入口表"
    )
    ap.add_argument("--scc", action="store_true", help="只打印强连通块明细")
    ap.add_argument("--why", metavar="MODULE", help="打印经过该模块的最短回路（定位环怎么闭合）")
    ap.add_argument("--json", metavar="PATH", help="把结果写成 JSON")
    ap.add_argument(
        "--assert-max", type=int, metavar="N", help="任一入口闭包行数 > N 则退出码 1（CI 闸门）"
    )
    ap.add_argument(
        "--no-lazy",
        action="store_true",
        help="忽略函数内惰性 import（诊断用：环是惰性 import 造的，还是顶层就有的）",
    )
    args = ap.parse_args(argv)

    modules = discover_modules()
    if not modules:
        print("未发现任何模块，检查 SCAN_ROOTS 或工作目录。", file=sys.stderr)
        return 2
    g = build_graph(modules, include_lazy=not args.no_lazy)
    entries = tuple(args.entry) if args.entry else DEFAULT_ENTRIES

    if args.why:
        path = explain_cycle(g, args.why)
        if path is None:
            print(f"{args.why} 不在任何环里。")
        else:
            print(f"经过 {args.why} 的最短回路（{len(path) - 1} 跳）:")
            for i, node in enumerate(path):
                print(f"  {'→ ' if i else '  '}{node}  ({g.sizes.get(node, 0):,} 行)")
        return 0

    if args.scc:
        report_scc(g)
    else:
        report_overview(g, entries)

    if args.json:
        payload = {
            "module_count": len(g.modules),
            "total_lines": sum(g.sizes.values()),
            "cycles": [
                {
                    "files": len(c),
                    "lines": sum(g.sizes.get(m, 0) for m in c),
                    "modules": sorted(c),
                }
                for c in g.strongly_connected()
            ],
            "closures": {
                e: {"files": g.closure_size(e)[0], "lines": g.closure_size(e)[1]}
                for e in entries
                if e in g.edges
            },
        }
        Path(args.json).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\nJSON 已写入 {args.json}")

    if args.assert_max is not None:
        over = []
        for e in entries:
            if e not in g.edges:
                continue
            lines = g.closure_size(e)[1]
            if lines > args.assert_max:
                over.append((e, lines))
        if over:
            print(f"\nFAIL 闭包超阈值 {args.assert_max:,} 行：", file=sys.stderr)
            for e, nl in over:
                print(f"    {e}: {nl:,}", file=sys.stderr)
            return 1
        print(f"\nOK 所有入口闭包 <= {args.assert_max:,} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
