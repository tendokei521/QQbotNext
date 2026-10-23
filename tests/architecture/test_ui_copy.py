"""界面文案闸门：`description` 必须像产品说明，不能是工程笔记。

## 规则（见 docs/UI_COPY.md）

1. **长度**：≤ 40 个汉字（Vuetify hint 是两行小字，再长没人读）
2. **禁内部黑话**：v3 / v2 / 迁移 / 接管 / 闸门 / 哨兵 / 槽 / 此前 / 曾经 / 仿 AstrBot …
3. **禁裸变量名**：`history_rounds`、`max_tokens`、`get_chat_history`（用户看不懂，看得懂的人也不需要）
4. **禁口语与指路**：「这个 Bot」「免得」「别让」「上面那张表」…
5. **禁说教**：「注意：」「记住」「说白了」

改动这些规则时同步改 `docs/UI_COPY.md`。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "app" / "agent" / "config_schema.py"

#: 说明长度上限（汉字数）
MAX_CHINESE = 40

#: 内部黑话：设计/迁移/框架内部概念，用户不该在界面上看到
JARGON = (
    "v3",
    "v2",
    "P4",
    "P5",
    "迁移",
    "接管",
    "闸门",
    "哨兵",
    "此前",
    "曾经",
    "历史上",
    "接口化",
    "模块化",
    "仿 AstrBot",
    "对齐 AstrBot",
    "legacy",
)

#: 口语 / 说教 / 指路
COLLOQUIAL = (
    "这个 Bot",
    "你自己",
    "咱们",
    "免得",
    "别让",
    "说白了",
    "注意：",
    # 跨控件指路（布局一变就失效），文案里不写"见上面那张表"这类话
    "上面的「",
    "下面的「",
    "上文提到",
    "如上所述",
)

#: 裸变量名（snake_case）——如 history_rounds / get_chat_history
_SNAKE = re.compile(r"\b[a-z][a-z0-9]*_[a-z0-9_]+\b")


def _chinese(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def _descriptions() -> list[tuple[str, str]]:
    tree = ast.parse(SCHEMA.read_text(encoding="utf-8"))
    out: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                continue
            if not isinstance(value, ast.Dict):
                continue
            for k, v in zip(value.keys, value.values):
                if (
                    isinstance(k, ast.Constant)
                    and k.value == "description"
                    and isinstance(v, ast.Constant)
                    and isinstance(v.value, str)
                ):
                    out.append((key.value, v.value))
    return out


def test_descriptions_are_short_enough():
    too_long = [
        f"{key}（{_chinese(desc)} 字）" for key, desc in _descriptions() if _chinese(desc) > MAX_CHINESE
    ]
    assert not too_long, "这些说明超过 40 汉字，请精简：\n  " + "\n  ".join(too_long)


def test_descriptions_have_no_internal_jargon():
    bad = []
    for key, desc in _descriptions():
        for word in JARGON:
            if word in desc:
                bad.append(f"{key}: 含「{word}」")
    assert not bad, "说明里出现了内部黑话：\n  " + "\n  ".join(bad)


def test_descriptions_have_no_bare_identifiers():
    bad = []
    for key, desc in _descriptions():
        hits = _SNAKE.findall(desc)
        if hits:
            bad.append(f"{key}: 裸变量名 {sorted(set(hits))}")
    assert not bad, "说明里出现了变量名/协议字段：\n  " + "\n  ".join(bad)


def test_descriptions_have_no_colloquial_phrases():
    bad = []
    for key, desc in _descriptions():
        for word in COLLOQUIAL:
            if word in desc:
                bad.append(f"{key}: 含口语/指路「{word}」")
    assert not bad, "说明里出现了口语或跨控件指路：\n  " + "\n  ".join(bad)


def test_zero_semantics_notation_is_consistent():
    """`0` 的含义必须显式写出，且写法统一为 `0 = X`（允许「0–23 时」这类数值区间）。"""
    bad = []
    for key, desc in _descriptions():
        if "0=" in desc:
            bad.append(f"{key}: 写成 0= ，应为 0 = ")
            continue
        # 出现 "0 = " 但右边没东西
        for m in re.finditer(r"0 =(\s*)([^\s，。；]*)", desc):
            if not m.group(2):
                bad.append(f"{key}: 0 = 后面没写含义")
    assert not bad, "0 值写法不统一：\n  " + "\n  ".join(bad)
