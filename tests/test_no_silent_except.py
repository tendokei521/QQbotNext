"""防止 `except Exception: pass`（静默吞异常）回流的守卫测试。

STYLE.md §7 明确规定"异常永不裸吞"：`except` 必须记录日志或显式处理。
历史代码中存在大量 `except Exception: pass`，导致真实故障（记忆不再更新、
历史读取跳过、连接未释放…）完全无迹可寻。本测试用 AST 扫描保证：
**不允许出现"异常体里只有 pass/continue/break/return None"的处理块**。

允许的例外（白名单）：日志调用本身的兜底保护——此时再抛异常会掩盖原始错误，
且会在日志器初始化失败时形成无限递归，属真正的"故意静默"，需带注释说明。
"""

from __future__ import annotations

import ast
import pathlib

import pytest

# 仓库根目录（tests/ 的上一级）
_ROOT = pathlib.Path(__file__).resolve().parent.parent
_SCAN_DIRS = ("app", "module")

# 允许静默的位置白名单：文件相对路径 -> 行号集合
# 说明：app/llm/knowledge/vector.py 的 _log_debug 是"日志失败的兜底"，
# 不能因为日志写不出去而中断向量检索主流程。
_ALLOWED_SILENT = {
    "app/llm/knowledge/vector.py": {25},
}


def _is_noop(node: ast.stmt) -> bool:
    """判断语句是否"什么都没做"。"""
    if isinstance(node, (ast.Pass, ast.Continue, ast.Break)):
        return True
    # return / return None
    if isinstance(node, ast.Return) and node.value is None:
        return True
    # ... (Ellipsis)
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and node.value.value is Ellipsis:
        return True
    return False


def _body_without_docstring(handler: ast.ExceptHandler) -> list[ast.stmt]:
    """去掉异常体里的纯字符串表达式（工具常插入的注解式字符串），保留真正语句。"""
    return [
        stmt
        for stmt in handler.body
        if not (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str)
        )
    ]


def _collect_silent_handlers() -> list[tuple[str, int]]:
    """扫描目标目录，返回 (相对路径, 行号) 列表：异常体全是 no-op 的宽泛 except。"""
    found: list[tuple[str, int]] = []
    for scan_dir in _SCAN_DIRS:
        base = _ROOT / scan_dir
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.ExceptHandler):
                    continue
                # 只看宽泛捕获：裸 except 或 except Exception/BaseException
                if not (
                    node.type is None
                    or (isinstance(node.type, ast.Name) and node.type.id in ("Exception", "BaseException"))
                ):
                    continue
                body = _body_without_docstring(node)
                if body and all(_is_noop(stmt) for stmt in body):
                    rel = path.relative_to(_ROOT).as_posix()
                    found.append((rel, node.lineno))
    return found


def test_no_silently_swallowed_exceptions():
    """不允许新增 `except Exception: pass` 这类静默吞异常的处理块。"""
    violations = [
        (path, lineno)
        for path, lineno in _collect_silent_handlers()
        if lineno not in _ALLOWED_SILENT.get(path, set())
    ]
    if violations:
        detail = "\n".join(f"  {p}:{ln}" for p, ln in violations)
        pytest.fail(
            "发现静默吞异常的 except 块（违反 STYLE.md §7「异常永不裸吞」）：\n"
            f"{detail}\n"
            "请改为记录日志（logger.debug/warning/exception），或加入 tests/test_no_silent_except.py "
            "的 _ALLOWED_SILENT 白名单并说明理由。"
        )


def test_allowed_silent_entries_still_exist():
    """白名单不应过期：若白名单指向的位置已不存在，应删掉条目，避免掩盖新问题。"""
    actual = {(p, ln) for p, ln in _collect_silent_handlers()}
    for path, lines in _ALLOWED_SILENT.items():
        for lineno in lines:
            assert (path, lineno) in actual, (
                f"白名单条目已失效（{path}:{lineno} 不再是静默 except），请从 _ALLOWED_SILENT 中移除"
            )
