#!/usr/bin/env python
"""测试审计：按 docs/TEST_POLICY.md 的口径标出"锁实现"的用例。

只读工具，不修改任何文件。用 AST 做启发式分类，输出每个文件的候选计数，
供人工（或批量脚本）决定删/合并/保留。

用法：
    venv\\Scripts\\python.exe scripts\\test_audit.py --report
    venv\\Scripts\\python.exe scripts\\test_audit.py --report --path tests\\agent
    venv\\Scripts\\python.exe scripts\\test_audit.py --json logs\\review\\test-audit.json
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = REPO_ROOT / "tests"

CJK = re.compile(r"[\u4e00-\u9fff]")

#: 断言里出现这些属性名，视为在断言 mock 的内部交互
MOCK_ASSERT_ATTRS = {
    "assert_called",
    "assert_called_once",
    "assert_called_with",
    "assert_called_once_with",
    "assert_any_call",
    "assert_not_called",
    "assert_awaited",
    "assert_awaited_once",
    "assert_awaited_with",
    "assert_awaited_once_with",
    "call_count",
    "call_args",
    "call_args_list",
}

#: 复杂度/结构类断言：len(...) == N
LEN_FUNCS = {"len"}


@dataclass
class Finding:
    path: str
    test: str
    kind: str
    line: int
    detail: str = ""


@dataclass
class FileReport:
    path: str
    tests: int = 0
    findings: list[Finding] = field(default_factory=list)
    plugin_coupled: bool = False

    @property
    def flagged(self) -> int:
        return len(self.findings)


class Visitor(ast.NodeVisitor):
    """收集单个测试函数内的候选断言。"""

    def __init__(self, path: str) -> None:
        self.path = path
        self.test = ""
        self.findings: list[Finding] = []
        self._seen: set[tuple[int, str]] = set()

    def _add(self, kind: str, node: ast.AST, detail: str = "") -> None:
        line = getattr(node, "lineno", 0)
        key = (line, kind)
        if key in self._seen:
            return
        self._seen.add(key)
        self.findings.append(Finding(self.path, self.test or "<module>", kind, line, detail))

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node.name.startswith("test"):
            prev = self.test
            self.test = node.name
            self.generic_visit(node)
            self.test = prev
        else:
            self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Compare(self, node: ast.Compare) -> None:
        for op, comparator in zip(node.ops, node.comparators, strict=False):
            if not isinstance(op, ast.Eq):
                continue
            left = node.left
            for a, b in ((left, comparator), (comparator, left)):
                if _is_long_cjk_constant(a) and not isinstance(b, ast.Constant):
                    self._add("copy_literal", node, _preview(a))
                if _is_len_call(b) and isinstance(a, ast.Constant) and isinstance(a.value, int):
                    self._add("count_snapshot", node, f"len(...) == {a.value}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in MOCK_ASSERT_ATTRS:
            self._add("mock_internal", node, node.attr)
        elif node.attr.startswith("_") and not node.attr.startswith("__"):
            self._add("private_api", node, node.attr)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if module.startswith("app"):
            for alias in node.names:
                if alias.name.startswith("_") and not alias.name.startswith("__"):
                    self._add("private_api", node, f"from {module} import {alias.name}")
        self.generic_visit(node)


def _is_long_cjk_constant(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and len(node.value) >= 12
        and bool(CJK.search(node.value))
        and "{" not in node.value
    )


def _is_len_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in LEN_FUNCS
    )


def _preview(node: ast.Constant) -> str:
    text = str(node.value).replace("\n", " ")
    return text[:28] + ("…" if len(text) > 28 else "")


def test_functions(tree: ast.Module) -> list[ast.AST]:
    out: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test"):
            out.append(node)
    return out


def has_assert(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            return True
        if isinstance(node, ast.Attribute) and node.attr in {"raises", "warns"}:
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "raises":
            return True
    return False


def audit_file(path: Path) -> FileReport:
    rel = path.relative_to(REPO_ROOT).as_posix()
    rep = FileReport(path=rel)
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError) as exc:  # pragma: no cover - 采集错误在此不致命
        rep.findings.append(Finding(rel, "<module>", "parse_error", 0, str(exc)))
        return rep

    rep.tests = len(test_functions(tree))
    rep.plugin_coupled = "app.plugins." in source
    visitor = Visitor(rel)
    visitor.visit(tree)
    rep.findings = visitor.findings
    if rep.tests and not has_assert(tree):
        rep.findings.append(Finding(rel, "<module>", "no_assert", 0, f"{rep.tests} 个用例无断言"))
    return rep


def iter_test_files(paths: list[Path]):
    for root in paths:
        if root.is_file():
            yield root
            continue
        yield from sorted(root.rglob("test_*.py"))


def render_markdown(reports: list[FileReport]) -> str:
    total_tests = sum(r.tests for r in reports)
    by_kind: dict[str, int] = {}
    for r in reports:
        for f in r.findings:
            by_kind[f.kind] = by_kind.get(f.kind, 0) + 1
    flagged_tests = {(r.path, f.test) for r in reports for f in r.findings if f.test != "<module>"}

    lines = [
        "# 测试审计报告",
        "",
        f"- 测试文件：**{len(reports)}**",
        f"- 用例：**{total_tests}**",
        f"- 命中候选的用例：**{len(flagged_tests)}**",
        f"- 候选按类型：{', '.join(f'{k}={v}' for k, v in sorted(by_kind.items(), key=lambda kv: -kv[1]))}",
        "",
        "## 候选最集中的文件",
        "",
        "| 文件 | 用例 | 命中 | copy | count | private | mock | 插件耦合 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    rows = sorted(reports, key=lambda r: (-r.flagged, -r.tests))[:40]
    for r in rows:
        counts = {k: sum(1 for f in r.findings if f.kind == k) for k in ("copy_literal", "count_snapshot", "private_api", "mock_internal")}
        lines.append(
            f"| `{r.path}` | {r.tests} | {r.flagged} | {counts['copy_literal']} | "
            f"{counts['count_snapshot']} | {counts['private_api']} | {counts['mock_internal']} | "
            f"{'是' if r.plugin_coupled else ''} |"
        )
    lines += ["", "## 明细（前 200 条）", ""]
    for r in reports:
        if not r.findings:
            continue
        lines.append(f"### `{r.path}`（{r.tests} 用例）")
        for f in r.findings[:8]:
            lines.append(f"- L{f.line} `{f.test}` **{f.kind}** {f.detail}")
        if len(r.findings) > 8:
            lines.append(f"- … 另有 {len(r.findings) - 8} 条")
        lines.append("")
        if len(lines) > 200:
            break
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按 docs/TEST_POLICY.md 口径审计测试")
    parser.add_argument("--path", action="append", default=[], help="限定目录/文件，可重复")
    parser.add_argument("--json", default="", help="输出 JSON 路径")
    parser.add_argument("--report", action="store_true", help="输出 markdown 报告到 stdout")
    parser.add_argument("--top", type=int, default=40, help="报告里列多少个文件")
    args = parser.parse_args(argv)

    roots = [REPO_ROOT / p for p in args.path] or [TESTS_DIR]
    reports = [audit_file(p) for p in iter_test_files(roots)]

    if args.json:
        out = REPO_ROOT / args.json
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = [
            {
                "path": r.path,
                "tests": r.tests,
                "plugin_coupled": r.plugin_coupled,
                "findings": [vars(f) for f in r.findings],
            }
            for r in reports
        ]
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写入 {out.relative_to(REPO_ROOT)}")

    if args.report:
        print(render_markdown(reports))
    else:
        total = sum(r.tests for r in reports)
        flagged = sum(len(r.findings) for r in reports)
        print(f"文件 {len(reports)}，用例 {total}，候选断言 {flagged}")
        print("加 --report 看明细（markdown），如：")
        print("  venv\\Scripts\\python.exe scripts\\test_audit.py --report --path tests\\agent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
