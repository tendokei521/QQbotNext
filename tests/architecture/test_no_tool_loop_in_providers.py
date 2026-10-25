"""工具循环**只许收敛、不许扩散**：登记已知副本，禁止新增。

## 背景

工具循环原有 6 份实现（见 `docs/tool-loop-refactor-plan.md` P1）：
3 处在调用方（`chat.stream_response` / `turn._stream_once` / `proactive.stream`），
3 处**在 provider 适配器内部**（openai_compat / anthropic / gemini）。

历史事故都出在这个结构上：改一处漏一处（`expand_image` 补图轮、补全登记、
空回复重试都曾只在一条路径修过）。P1 之后循环只剩一处，本文件的白名单随之清空。

## 规矩

- 新增一份循环 → 测试红（必须显式登记，且理由要写进白名单）；
- 白名单里的文件真的没有循环了 → 测试红（提醒把条目删掉，让收敛永不被撤回）。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"

#: 循环体模式。各处的上限变量名不同（`max_tool_rounds` / `max_rounds` / `rounds` /
#: `req.max_rounds`），但**循环形状**一致：计数器名字里带 round 的 `for ... in range(...)`。
#: 认形状而不是认上限变量名，否则"把上限改个名"就能绕过这条约束。
#:
#: 为什么要求计数器带 round：`app/domain/tags.py` 里有个 `for _ in range(max_rounds)`
#: 是标签剥离的嵌套上限，与工具轮无关；用计数器名把它排除掉，比维护忽略清单更稳。
_LOOP_RE = re.compile(r"^\s*for\s+\w*round\w*\s+in\s+range\(", re.MULTILINE)

#: 已知的循环副本（路径 → 归属说明）。P1 每收口一处就删一条，直到只剩共享 runner。
#: 进度：turn 流式 / 主动消息流式 / 主聊天流式 已收口（`loop/tool_step`）。
KNOWN_LOOPS: dict[str, str] = {
    #: 工具循环**唯一**的合法所在地（流式 + 非流式两个 runner）
    "app/agent/loop/tool_step.py": "共享步骤运行器 —— 循环只许住在这里",
    "app/agent/providers/openai_compat.py": "非流式（OpenAI 兼容）—— 应收口到 loop 层",
    "app/agent/providers/anthropic.py": "非流式（Claude）—— 应收口到 loop 层",
    "app/agent/providers/gemini.py": "非流式（Gemini）—— 应收口到 loop 层",
}

#: 副本数只许减少。P1 完成后只剩共享 runner（`loop/tool_step.py` 的流式 + 非流式两处）。
BASELINE_COUNT = 5


def _files_with_loop() -> dict[str, int]:
    found: dict[str, int] = {}
    for path in APP.rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):  # pragma: no cover - 读不到就跳过
            continue
        hits = len(_LOOP_RE.findall(text))
        if hits:
            found[path.relative_to(ROOT).as_posix()] = hits
    return found


def test_no_unregistered_tool_loop():
    """出现没登记的循环副本 → 失败（先想清楚为什么要多一份）。"""
    found = _files_with_loop()
    unregistered = sorted(set(found) - set(KNOWN_LOOPS))
    assert not unregistered, (
        "新增了未登记的工具循环："
        + "、".join(unregistered)
        + "。工具循环只许收敛到 loop 层，请复用共享 runner，或先更新本测试的白名单与理由。"
    )


def test_known_loops_still_exist_until_collapsed():
    """登记了却已经没有循环 → 失败（说明收敛完成，白名单该删条目了）。"""
    found = _files_with_loop()
    stale = sorted(set(KNOWN_LOOPS) - set(found))
    assert not stale, (
        "白名单里这些文件已经没有工具循环了："
        + "、".join(stale)
        + "。请把它们从 KNOWN_LOOPS 删掉，并把 BASELINE_COUNT 调小——收敛不许被悄悄撤回。"
    )


def test_loop_copy_count_does_not_grow():
    """总数只许减不许增。"""
    total = sum(_files_with_loop().values())
    assert total <= BASELINE_COUNT, (
        f"工具循环副本数从 {BASELINE_COUNT} 涨到 {total}："
        "这是本次重构要消灭的结构性欠账，不允许再加一份。"
    )
