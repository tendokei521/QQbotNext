"""工具循环**只许收敛、不许扩散**：登记已知副本，禁止新增。

## 背景

工具循环目前有 6 份实现（见 `docs/tool-loop-refactor-plan.md` P1）：
3 处在调用方（`chat.stream_response` / `turn._stream_once` / `proactive.stream`），
3 处**在 provider 适配器内部**（openai_compat / anthropic / gemini）。

历史事故都出在这个结构上：改一处漏一处（`expand_image` 补图轮、补全登记、
空回复重试都曾只在一条路径修过）。P1 之后循环只剩一处，本文件的白名单随之清空。

## 规矩

- 新增一份循环 → 测试红（必须显式登记，且理由要写进白名单）；
- 白名单里的文件真的没有循环了 → 测试红（提醒把条目删掉，让收敛永不被撤回）。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"

#: 循环体模式。各处的上限变量名不同（`max_tool_rounds` / `max_rounds` / `rounds`），
#: 但**循环形状**一致：`for _round in range(...)`。认形状而不是认变量名，
#: 否则"把上限改个名"就能绕过这条约束。
_LOOP_PATTERN = "for _round in range("

#: 已知的 6 处（P1 收口后应为空）。路径 → 归属说明
KNOWN_LOOPS: dict[str, str] = {
    "app/agent/loop/chat.py": "流式主路径（含发言通道 flush / 收敛重试）",
    "app/agent/loop/turn.py": "流式无发送版（on_sentence 回调）",
    "app/plugins/llm_proactive/stream.py": "主动消息 / 定时任务流式",
    "app/agent/providers/openai_compat.py": "非流式（OpenAI 兼容）—— 应收口到 loop 层",
    "app/agent/providers/anthropic.py": "非流式（Claude）—— 应收口到 loop 层",
    "app/agent/providers/gemini.py": "非流式（Gemini）—— 应收口到 loop 层",
}

#: 副本数只许减少。P1 完成后改成 1（只剩共享 runner）。
BASELINE_COUNT = 6


def _files_with_loop() -> dict[str, int]:
    found: dict[str, int] = {}
    for path in APP.rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):  # pragma: no cover - 读不到就跳过
            continue
        hits = text.count(_LOOP_PATTERN)
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
