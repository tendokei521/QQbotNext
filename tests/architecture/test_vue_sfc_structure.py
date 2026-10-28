"""Vue SFC 结构闸门：`<script setup>` 之外不能有游离代码。

## 为什么需要这层闸门

SFC 解析器只认 `<script>` / `<template>` / `<style>` 三个块，块外文本会被**静默忽略**。
真实事故：三个 Tools 页在 `<script setup>` 之前各粘了一段 `toggleTool` 函数——
模板调用的是块内那份实现，块外这份永远不执行；因为语法上"合法"，`vue-tsc` 也不报错，
只能靠扫描文件结构拦住。

纯 Python + 正则实现（不引入前端依赖），风格与 `test_ui_copy.py` 一致：
`app/webui/dashboard/src/**/*.vue` 全量扫，不挑文件。

## 规则

1. 块外只允许空白与 `<!-- -->` 注释（游离代码 = 事故现场）
2. 文件第一个非空字符必须是 `<`（第一个块从文件开头开始）
3. 每个 .vue 至多 1 个 `<script setup>`；同时存在普通 `<script>` 时，普通 script 必须在 setup 之前
4. 三个 Tools 页的 `toggleTool` 恰好定义 1 次，且位于 `<script setup>` 内
5. `<script setup>` 内顶层 `function X(` 不重名（重复定义说明粘错了一份）
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "app" / "webui" / "dashboard" / "src"

#: SFC 顶层块开标签：script / template / style
_BLOCK_TAG = re.compile(r"<(?P<tag>script|template|style)\b(?P<attrs>[^>]*)>")
#: template 的嵌套标签（template 内部允许 <template v-if> / <template #slot>）
_TEMPLATE_TAG = re.compile(r"</?template\b[^>]*>")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
#: `<script setup>` 的判定：属性里出现独立的 setup
_SETUP_ATTR = re.compile(r"\bsetup\b")

#: 本次事故的三份文件（回归点）
TOOL_PAGES = ("SystemToolsPage.vue", "McpToolsPage.vue", "ModuleToolsPage.vue")


def _vue_files() -> list[Path]:
    files = sorted(SRC.rglob("*.vue"))
    assert files, f"没有扫到任何 .vue 文件，路径不对？{SRC}"
    return files


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _template_end(src: str, start: int) -> int:
    """返回顶层 `<template>` 的结束偏移（结束标签之后）；未闭合返回 -1。

    SFC 模板内部可以嵌套 `<template v-if>`，所以不能用非贪婪正则直接截，
    要按标签配对计数。
    """
    depth = 0
    for match in _TEMPLATE_TAG.finditer(src, start):
        token = match.group(0)
        if token.startswith("</"):
            depth -= 1
            if depth == 0:
                return match.end()
        elif token.endswith("/>"):
            continue  # <template /> 自闭合，不改变深度
        else:
            depth += 1
    return -1


def _block_spans(src: str) -> list[tuple[str, str, int, int]]:
    """返回顶层块 `(tag, attrs, start, end)` 列表，end 为结束标签之后的偏移。

    按出现顺序消费块；遇到未闭合的块就停在最后一个合法块之后，
    剩下的部分会被"块外残留"检查逮到。
    """
    spans: list[tuple[str, str, int, int]] = []
    cursor = 0
    while True:
        match = _BLOCK_TAG.search(src, cursor)
        if match is None:
            return spans
        tag = match.group("tag")
        attrs = match.group("attrs") or ""
        if tag == "template":
            end = _template_end(src, match.start())
        else:
            close = src.find(f"</{tag}>", match.end())
            end = close + len(f"</{tag}>") if close != -1 else -1
        if end < 0:
            return spans
        spans.append((tag, attrs, match.start(), end))
        cursor = end


def _mask_blocks(src: str) -> str:
    """把块内容替换成空格（保留换行），偏移与行号不变。"""
    chars = list(src)
    for _, _, start, end in _block_spans(src):
        for i in range(start, end):
            if chars[i] != "\n":
                chars[i] = " "
    return "".join(chars)


def _mask_comments(text: str) -> str:
    """把 HTML 注释替换成空格（保留换行），偏移与行号不变。"""

    def _blank(match: re.Match[str]) -> str:
        return "".join("\n" if ch == "\n" else " " for ch in match.group(0))

    return _HTML_COMMENT.sub(_blank, text)


def _stray_lines(src: str) -> list[tuple[int, str]]:
    """返回块外/注释之外的非空行 `(行号, 原文行)`。"""
    masked = _mask_comments(_mask_blocks(src))
    source_lines = src.splitlines()
    stray: list[tuple[int, str]] = []
    for lineno, line in enumerate(masked.splitlines(), 1):
        if line.strip():
            original = source_lines[lineno - 1] if lineno - 1 < len(source_lines) else line
            stray.append((lineno, original.strip()))
    return stray


def test_no_code_outside_sfc_blocks():
    """块外只能有空白和 HTML 注释——游离函数/代码一律拦下。"""
    bad: list[str] = []
    for path in _vue_files():
        src = path.read_text(encoding="utf-8")
        for lineno, line in _stray_lines(src):
            bad.append(f"{_rel(path)}:{lineno}: {line[:60]}")
    assert not bad, (
        "SFC 块外有会被解析器静默忽略的游离内容（块内才是真正执行的代码）：\n  "
        + "\n  ".join(bad)
    )


def test_first_block_starts_file():
    """文件第一个非空字符必须是 `<`，块前不能有游离文本/代码。"""
    bad: list[str] = []
    for path in _vue_files():
        src = path.read_text(encoding="utf-8")
        stripped = src.lstrip("\ufeff \t\r\n")
        if stripped.startswith("<"):
            continue
        first_line = next((line.strip() for line in src.splitlines() if line.strip()), "")
        bad.append(f"{_rel(path)}: 文件开头有游离内容 —— {first_line[:60]}")
    assert not bad, (
        "SFC 必须从块标签开始，块前的文本不会被解析：\n  " + "\n  ".join(bad)
    )


def test_single_script_setup_block():
    """每个 .vue 至多 1 个 `<script setup>`；普通 `<script>` 必须在 setup 之前。"""
    bad: list[str] = []
    for path in _vue_files():
        src = path.read_text(encoding="utf-8")
        scripts = [
            (start, _SETUP_ATTR.search(attrs) is not None, attrs)
            for tag, attrs, start, end in _block_spans(src)
            if tag == "script"
        ]
        setup = [item for item in scripts if item[1]]
        plain = [item for item in scripts if not item[1]]
        if len(setup) > 1:
            bad.append(f"{_rel(path)}: <script setup> 出现 {len(setup)} 个")
        if plain and setup and plain[0][0] > setup[0][0]:
            bad.append(f"{_rel(path)}: 普通 <script> 必须写在 <script setup> 之前")
    assert not bad, (
        "SFC script 块结构不符合 Vue 要求：\n  " + "\n  ".join(bad)
    )


def test_tool_pages_define_toggle_tool_once():
    """回归：三个 Tools 页的 toggleTool 只能定义 1 次，且在 <script setup> 内。"""
    pattern = re.compile(r"\bfunction\s+toggleTool\s*\(")
    bad: list[str] = []
    for name in TOOL_PAGES:
        path = SRC / "views" / name
        assert path.is_file(), f"文件不存在：{_rel(path)}"
        src = path.read_text(encoding="utf-8")
        matches = list(pattern.finditer(src))
        if len(matches) != 1:
            bad.append(f"{_rel(path)}: function toggleTool 出现 {len(matches)} 次（应为 1 次）")
        setup_spans = [
            (start, end)
            for tag, attrs, start, end in _block_spans(src)
            if tag == "script" and _SETUP_ATTR.search(attrs) is not None
        ]
        if not setup_spans:
            bad.append(f"{_rel(path)}: 找不到 <script setup> 块")
            continue
        start, end = setup_spans[0]
        outside = [m.start() for m in matches if not start <= m.start() < end]
        if outside:
            bad.append(f"{_rel(path)}: toggleTool 有定义在 <script setup> 之外")
    assert not bad, (
        "toggleTool 必须在 <script setup> 内且只定义一次（块外那份永远不会执行）：\n  "
        + "\n  ".join(bad)
    )


def test_no_duplicate_top_level_function_names():
    """`<script setup>` 内顶层 `function X(` 不重名。"""
    pattern = re.compile(r"(?m)^function\s+([A-Za-z_$][\w$]*)\s*\(")
    bad: list[str] = []
    for path in _vue_files():
        src = path.read_text(encoding="utf-8")
        for tag, attrs, start, end in _block_spans(src):
            if tag != "script" or _SETUP_ATTR.search(attrs) is None:
                continue
            content = src[start:end]
            names = pattern.findall(content)
            duplicated = sorted({name for name in names if names.count(name) > 1})
            if duplicated:
                bad.append(f"{_rel(path)}: <script setup> 内顶层函数重名 {duplicated}")
    assert not bad, (
        "同一 <script setup> 内同名顶层函数只应保留一份：\n  " + "\n  ".join(bad)
    )
