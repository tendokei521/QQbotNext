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
MODULE_SCHEMAS = ROOT / "app" / "plugins"

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


def _module_descriptions() -> list[tuple[str, str]]:
    """模块配置页渲染的是模块自己那份 schema（`app/plugins/*/config_schema.py`）。"""
    out: list[tuple[str, str]] = []
    for path in sorted(MODULE_SCHEMAS.glob("*/config_schema.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
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
                        out.append((f"{path.parent.name}.{key.value}", v.value))
    return out


def _all_descriptions() -> list[tuple[str, str]]:
    return _descriptions() + _module_descriptions()


def _snake_hits(text: str) -> list[str]:
    """只禁"正文里蹦出来的标识符"；`反引号` 与 {{占位符}} 属用户语法。"""
    cleaned = re.sub(r"`[^`]*`", " ", text)
    cleaned = re.sub(r"\{\{.*?\}\}", " ", cleaned)
    return _SNAKE.findall(cleaned)


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
        f"{key}（{_chinese(desc)} 字）"
        for key, desc in _all_descriptions()
        if _chinese(desc) > MAX_CHINESE
    ]
    assert not too_long, "这些说明超过 40 汉字，请精简：\n  " + "\n  ".join(too_long)


def test_descriptions_have_no_internal_jargon():
    bad = []
    for key, desc in _all_descriptions():
        for word in JARGON:
            if word in desc:
                bad.append(f"{key}: 含「{word}」")
    assert not bad, "说明里出现了内部黑话：\n  " + "\n  ".join(bad)


def test_descriptions_have_no_bare_identifiers():
    bad = []
    for key, desc in _all_descriptions():
        hits = _snake_hits(desc)
        if hits:
            bad.append(f"{key}: 裸变量名 {sorted(set(hits))}")
    assert not bad, "说明里出现了变量名/协议字段：\n  " + "\n  ".join(bad)


def test_descriptions_have_no_colloquial_phrases():
    bad = []
    for key, desc in _all_descriptions():
        for word in COLLOQUIAL:
            if word in desc:
                bad.append(f"{key}: 含口语/指路「{word}」")
    assert not bad, "说明里出现了口语或跨控件指路：\n  " + "\n  ".join(bad)


def test_zero_semantics_notation_is_consistent():
    """`0` 的含义必须显式写出，且写法统一为 `0 = X`（允许「0–23 时」这类数值区间）。"""
    bad = []
    for key, desc in _all_descriptions():
        if "0=" in desc:
            bad.append(f"{key}: 写成 0= ，应为 0 = ")
            continue
        # 出现 "0 = " 但右边没东西
        for m in re.finditer(r"0 =(\s*)([^\s，。；]*)", desc):
            if not m.group(2):
                bad.append(f"{key}: 0 = 后面没写含义")
    assert not bad, "0 值写法不统一：\n  " + "\n  ".join(bad)


# ==================== 前端定制卡片 ====================


def _strip_markup(html: str) -> str:
    """去掉标签与 Vue 插值，只留人读到的那点文字。"""
    text = re.sub(r"\{\{.*?\}\}", " ", html, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


#: 需要检查的定制卡片（用户直接读到的说明文字）
CARD_DIR = ROOT / "app" / "webui" / "dashboard" / "src" / "components" / "agent" / "sections"

#: 卡片说明里不该出现的说法（v3 后这些功能已剥离成独立模块）
STALE_CLAIMS = (
    "定时任务与主动消息同时停止",
    "定时任务和主动消息同时停止",
    "定时任务也会停止",
    "主动消息也会停止",
)


def _card_texts() -> list[tuple[str, str]]:
    """抽取卡片里 `v-card-text` 的中文段落。"""
    out: list[tuple[str, str]] = []
    if not CARD_DIR.is_dir():
        return out
    for path in CARD_DIR.glob("*.vue"):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"<v-card-text[^>]*>(.*?)</v-card-text>", text, re.S):
            body = _strip_markup(m.group(1))
            if _chinese(body) >= 4:
                out.append((path.name, body))
    return out


def test_frontend_card_copy_is_compliant():
    """卡片说明也要短、说人话，且不写跨控件指路。"""
    bad = []
    for name, body in _card_texts():
        if _chinese(body) > MAX_CHINESE:
            bad.append(f"{name}: {_chinese(body)} 字过长 —— {body[:40]}…")
        for word in COLLOQUIAL:
            if word in body:
                bad.append(f"{name}: 含口语/指路「{word}」")
        for word in JARGON:
            if word in body:
                bad.append(f"{name}: 含内部黑话「{word}」")
    assert not bad, "卡片文案不合规：\n  " + "\n  ".join(bad)


def test_card_copy_has_no_stale_module_claims():
    """定时任务 / 主动消息是独立模块，不能被描述成"跟着 Agent 一起停"。"""
    bad = []
    for name, body in _card_texts():
        for claim in STALE_CLAIMS:
            if claim in body:
                bad.append(f"{name}: 错误联动「{claim}」")
    assert not bad, (
        "这些说法与实现不符（定时任务/主动消息已剥离为独立模块，各自启停）：\n  "
        + "\n  ".join(bad)
    )
