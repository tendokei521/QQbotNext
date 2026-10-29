"""前端界面文案闸门：`.vue` 模板文本与用户可见字面量不能出现开发者文案。

## 为什么需要这层闸门

`test_ui_copy.py` 只守得住后端 schema 里的 `description`；界面上大量文字直接写在
`.vue` 模板里（标签、按钮、提示、空态、占位符），没有任何自动检查。本轮人工审计
翻出几十处「接管 / 工件 / 裸 snake_case / 旧页面名」，靠人肉复扫不可持续——
文案回归和代码回归一样需要闸门。

## 扫描范围

- `app/webui/dashboard/src/**/*.vue` 的 `<template>` 块：
  - **文本节点**：剥掉 `{{ 插值 }}`、HTML 标签与注释后，用户真正读到的那部分文字；
  - **用户可见属性字面量**：`label` / `title` / `placeholder` / `hint` / `alt` 的
    静态值（`:label=` / `v-bind:label=` 这类动态绑定不算，值里带 `{` / `:` 的也不算）；
  - **模板表达式里的中文字面量**：`{{ cond ? '已加载' : '未加载' }}`、
    `:items="[{ title: '对话' }]"` 这类写在表达式里的用户文案。
    文案检查的通用口径是"剥掉插值"；但很多状态/按钮文案就写在插值里的三元表达式里，
    剥掉后形成盲区，所以这里补扫引号里的中文字面量。
- `app/webui/dashboard/src/agent/sections.json` 的 `title` / `subtitle`。

`<script>` / `<style>` 块**不扫**：代码、注释、类型名里出现这些词是正常的，
「注释/代码里的词不算文本」是本闸门明确的口径。

## 规则（见 docs/UI_COPY.md）

1. **禁内部黑话**：v3 / v4 / P4 / P5 / 迁移 / 接管 / 闸门 / 哨兵 / 接口化 / 模块化 /
   legacy / 此前 / 曾经 / 历史上 / 语义化 / 工件 / 装配 / 槽位 / schema / 枚举 /
   兜底 / 抖动。
2. **禁裸 snake_case 标识符**：`group_enable`、`max_tokens` 这类内部字段名不出现
   在用户可见文字里。
3. **长度**：属性字面量单个 ≤ 40 个汉字；模板文本按句子（`。；！？` 切分）≤ 60 个汉字
   （整段长说明允许，但一句话不该长到没人读）。
4. **禁已统一的旧页面名**：System Tools / MCP Tools / OneBot Tools / Module Tools /
   Tool 管理 / Provider 预设 / Agent 面板 / 功能模块 / 预设中心。

失败信息给出 `文件:行号 + 命中词 + 原文片段`，直接定位到要改的那句话。

风格与 `test_ui_copy.py` / `test_vue_sfc_structure.py` 一致：纯 Python + 正则，
不引入任何前端依赖。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "app" / "webui" / "dashboard" / "src"
SECTIONS_JSON = SRC / "agent" / "sections.json"
AI_PANEL_JSON = SRC / "agent" / "ai-panel.json"

#: 内部黑话：设计/框架内部概念，用户不该在界面上看到
JARGON_CN = (
    "迁移",
    "接管",
    "闸门",
    "哨兵",
    "接口化",
    "模块化",
    "此前",
    "曾经",
    "历史上",
    "语义化",
    "工件",
    "装配",
    "槽位",
    "枚举",
    "兜底",
    "抖动",
    "框架级",
    "后端",
    "上游",
    "落库",
)

#: 英文黑话按词边界匹配，避免误伤正常英文单词
JARGON_EN = (
    r"\bv[34]\b",
    r"\bP[45]\b",
    r"\blegacy\b",
    r"\bschema\b",
)

#: 已统一的旧页面名（新名字见 DefaultLayout 侧边栏）
OLD_LABELS = (
    "System Tools",
    "MCP Tools",
    "OneBot Tools",
    "Module Tools",
    "Tool 管理",
    "Provider 预设",
    "Agent 面板",
    "群聊权限",
    "AI 对话设置",
    "功能模块",
    "预设中心",
)

#: 属性字面量的汉字上限（一个 label/hint 不该超过两行小字）
ATTR_LIMIT = 40
#: 模板文本一句话的汉字上限（整段可放宽，单句不行）
TEXT_LIMIT = 60

#: 用户可见的属性字面量；`:label=` / `v-bind:label=` 由前置字符排除
_ATTR = re.compile(r'(?<![:@\w-])(?P<name>label|title|placeholder|hint|alt)="(?P<value>[^"]*)"')
#: 两个标签之间的文本节点（模板已保证没有 `<` 裸文本）
_TEXT = re.compile(r">(?P<text>[^<>]+)<")
#: 模板表达式里的中文字符串字面量（`{{ ... }}` 与 `:prop="..."` 里的三元/数组字面量）
_STRING = re.compile(r"'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\"", re.S)
#: 裸 snake_case 标识符
_SNAKE = re.compile(r"\b[a-z][a-z0-9]*_[a-z0-9_]+\b")
#: Vue 插值
_INTERP = re.compile(r"\{\{.*?\}\}", re.S)
#: HTML 注释
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
#: SFC 顶层块开标签
_BLOCK = re.compile(r"<(?P<tag>script|template|style)\b")
#: 模板里允许嵌套的 `<template v-if>`；用配对计数找顶层结束
_TEMPLATE_TAG = re.compile(r"</?template\b[^>]*>")
#: 断句符：长度按句子算
_SENTENCE_SPLIT = re.compile(r"[。；！？]")
#: XML/HTML 实体：还原成人读的字再判断（`&amp;` 之类不参与规则）
_ENTITY = re.compile(r"&(?:amp|lt|gt|quot|#\d+);")


def _vue_files() -> list[Path]:
    files = sorted(SRC.rglob("*.vue"))
    assert files, f"没有扫到任何 .vue 文件，路径不对？{SRC}"
    return files


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _chinese(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _snippet(text: str, limit: int = 50) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def _blank(text: str) -> str:
    """把一段内容替换成等长空格（保留换行），保证行号/偏移不变。"""
    return "".join("\n" if ch == "\n" else " " for ch in text)


def _template_end(src: str, start: int) -> int:
    """返回顶层 `<template>` 的结束偏移；未闭合返回 -1。"""
    depth = 0
    for match in _TEMPLATE_TAG.finditer(src, start):
        token = match.group(0)
        if token.startswith("</"):
            depth -= 1
            if depth == 0:
                return match.end()
        else:
            depth += 1
    return -1


def _template_only(src: str) -> str:
    """只保留 `<template>` 块的内容，其余（script/style/块外）抹成空格。

    行号与原文一致，失败信息里可以直接报行号。注释与插值保持原样，
    由调用方决定是否抹掉（文本检查要抹，表达式字面量检查要留）。
    """
    chars = list(src)
    masked = [False] * len(src)

    def _mask(start: int, end: int) -> None:
        for i in range(start, end):
            masked[i] = True

    cursor = 0
    while True:
        match = _BLOCK.search(src, cursor)
        if match is None:
            break
        tag = match.group("tag")
        if tag == "template":
            end = _template_end(src, match.start())
        else:
            close = src.find(f"</{tag}>", match.end())
            end = close + len(f"</{tag}>") if close != -1 else -1
        if end < 0:
            break
        if tag != "template":
            _mask(match.start(), end)
        cursor = end

    for i, is_masked in enumerate(masked):
        if is_masked:
            chars[i] = "\n" if chars[i] == "\n" else " "
    return "".join(chars)


def _mask_comments(text: str) -> str:
    return _HTML_COMMENT.sub(lambda m: _blank(m.group(0)), text)


def _units(masked: str) -> list[tuple[int, str, str]]:
    """返回 `(行号, 文案, 类型)` 列表；类型为 `text` 或 `attr`。"""
    out: list[tuple[int, str, str]] = []
    for match in _TEXT.finditer(masked):
        cleaned = " ".join(_ENTITY.sub(" ", match.group("text")).split())
        if cleaned:
            out.append((_line_of(masked, match.start("text")), cleaned, "text"))
    for match in _ATTR.finditer(masked):
        value = match.group("value")
        # 动态绑定（含插值/表达式）不是静态文案，交给表达式字面量检查
        if "{" in value or ":" in value:
            continue
        cleaned = " ".join(_ENTITY.sub(" ", value).split())
        if cleaned:
            out.append((_line_of(masked, match.start("value")), cleaned, f"attr:{match.group('name')}"))
    return out


def _all_units() -> list[tuple[str, int, str, str]]:
    out: list[tuple[str, int, str, str]] = []
    for path in _vue_files():
        # 文本/属性检查看不到 `{{ ... }}` 里的字符串，这里先把插值抹掉
        raw = _template_only(path.read_text(encoding="utf-8"))
        masked = _INTERP.sub(lambda m: _blank(m.group(0)), _mask_comments(raw))
        rel = _rel(path)
        for lineno, text, kind in _units(masked):
            out.append((rel, lineno, text, kind))
    return out


def _expression_strings() -> list[tuple[str, int, str]]:
    """模板表达式里的中文字面量。

    很多用户可见文案写在 `{{ cond ? '已加载' : '未加载' }}`、`{{ x.join('、') }}`
    或 `:items="[{ title: '对话' }]"` 里，剥掉插值会形成检查盲区，所以单独扫
    **模板块内、`=` 后面紧跟的静态属性值除外**的引号字符串。
    """
    out: list[tuple[str, int, str]] = []
    for path in _vue_files():
        masked = _mask_comments(_template_only(path.read_text(encoding="utf-8")))
        for match in _STRING.finditer(masked):
            value = match.group(1) if match.group(1) is not None else match.group(2)
            if not re.search(r"[\u4e00-\u9fff]", value):
                continue
            # 静态属性值（`label="中文"`）由属性字面量检查负责，这里跳过免得重复报
            if match.start() and masked[match.start() - 1] == "=":
                continue
            out.append((_rel(path), _line_of(masked, match.start()), value))
    return out


def _all_copy_units() -> list[tuple[str, int, str, str]]:
    """模板文本 + 属性字面量 + 表达式里的中文字面量，统一成待检单元。"""
    return _all_units() + [
        (rel, lineno, text, "expr") for rel, lineno, text in _expression_strings()
    ]


def _jargon_hits(text: str) -> list[str]:
    hits = [word for word in JARGON_CN if word in text]
    hits += [pattern for pattern in JARGON_EN if re.search(pattern, text, re.I)]
    return hits


def test_vue_templates_have_no_internal_jargon():
    """模板文本/字面量里不能出现内部黑话。"""
    bad: list[str] = []
    for rel, lineno, text, _kind in _all_copy_units():
        hits = _jargon_hits(text)
        if hits:
            bad.append(f"{rel}:{lineno}: 含黑话 {hits} —— {_snippet(text)}")
    assert not bad, "界面文案里出现了内部黑话（代码/注释里的不算）：\n  " + "\n  ".join(bad)


def test_vue_templates_have_no_bare_identifiers():
    """模板文本/字面量里不能出现裸 snake_case 字段名。"""
    bad: list[str] = []
    for rel, lineno, text, _kind in _all_copy_units():
        hits = _SNAKE.findall(text)
        if hits:
            bad.append(f"{rel}:{lineno}: 裸标识符 {sorted(set(hits))} —— {_snippet(text)}")
    assert not bad, "界面文案里出现了内部字段名（用户看不懂）：\n  " + "\n  ".join(bad)


def test_vue_templates_have_no_old_page_names():
    """已统一的旧页面名不能回潮。"""
    bad: list[str] = []
    for rel, lineno, text, _kind in _all_copy_units():
        hits = [label for label in OLD_LABELS if label.lower() in text.lower()]
        if hits:
            bad.append(f"{rel}:{lineno}: 旧页面名 {hits} —— {_snippet(text)}")
    assert not bad, (
        "界面文案里出现了已统一的旧页面名（新名字见 DefaultLayout 侧边栏）：\n  "
        + "\n  ".join(bad)
    )


def test_vue_copy_is_short_enough():
    """属性字面量 ≤ 40 汉字；模板文本/表达式字面量按句子 ≤ 60 汉字。"""
    bad: list[str] = []
    for rel, lineno, text, kind in _all_copy_units():
        if kind.startswith("attr:"):
            count = _chinese(text)
            if count > ATTR_LIMIT:
                bad.append(f"{rel}:{lineno}: {kind} {count} 字（上限 {ATTR_LIMIT}）—— {_snippet(text)}")
            continue
        for sentence in _SENTENCE_SPLIT.split(text):
            count = _chinese(sentence)
            if count > TEXT_LIMIT:
                bad.append(
                    f"{rel}:{lineno}: 单句 {count} 字（上限 {TEXT_LIMIT}）—— {_snippet(sentence)}"
                )
    assert not bad, (
        "界面文案过长（属性 ≤ 40 汉字、模板单句 ≤ 60 汉字，超了没人读）：\n  "
        + "\n  ".join(bad)
    )


def test_sections_json_copy_is_compliant():
    """sections.json 的分区标题/副标题同样要过文案规则。"""
    assert SECTIONS_JSON.is_file(), f"文件不存在：{_rel(SECTIONS_JSON)}"
    data = json.loads(SECTIONS_JSON.read_text(encoding="utf-8"))
    bad: list[str] = []
    for section in data.get("sections", []):
        for key in ("title", "subtitle"):
            text = str(section.get(key) or "")
            if not text:
                continue
            hits = _jargon_hits(text)
            hits += [label for label in OLD_LABELS if label.lower() in text.lower()]
            hits += sorted(set(_SNAKE.findall(text)))
            if hits:
                bad.append(
                    f"{_rel(SECTIONS_JSON)}: {section.get('id')}.{key} 含 {hits} —— {_snippet(text)}"
                )
            if _chinese(text) > TEXT_LIMIT:
                bad.append(
                    f"{_rel(SECTIONS_JSON)}: {section.get('id')}.{key} 过长 "
                    f"({_chinese(text)} 字 > {TEXT_LIMIT}) —— {_snippet(text)}"
                )
    assert not bad, (
        "sections.json 的分区标题/副标题含开发者文案或过长：\n  " + "\n  ".join(bad)
    )


def test_ai_panel_json_copy_is_compliant():
    """ai-panel.json（AI 对话面板/回复与权限）的卡片与分组名同样过文案规则。"""
    assert AI_PANEL_JSON.is_file(), f"文件不存在：{_rel(AI_PANEL_JSON)}"
    data = json.loads(AI_PANEL_JSON.read_text(encoding="utf-8"))
    labels: list[tuple[str, str]] = []
    for card in data.get("panel", {}).get("cards", []):
        labels.append((f"panel.{card.get('id')}", str(card.get("label") or "")))
    for group in data.get("reply", {}).get("groups", []):
        labels.append((f"reply.{group.get('id')}", str(group.get("label") or "")))
    bad: list[str] = []
    for where, text in labels:
        if not text:
            continue
        hits = _jargon_hits(text)
        hits += [label for label in OLD_LABELS if label.lower() in text.lower()]
        hits += sorted(set(_SNAKE.findall(text)))
        if hits:
            bad.append(f"{_rel(AI_PANEL_JSON)}: {where} 含 {hits} —— {_snippet(text)}")
        if _chinese(text) > TEXT_LIMIT:
            bad.append(f"{_rel(AI_PANEL_JSON)}: {where} 过长（{_chinese(text)} 字）—— {_snippet(text)}")
    assert not bad, "ai-panel.json 的卡片/分组名含开发者文案或过长：\n  " + "\n  ".join(bad)
