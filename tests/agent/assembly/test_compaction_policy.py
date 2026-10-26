"""Q1：压缩由 token 压力驱动 + 可插拔后端 + 括号事件。

钉住五件事：

1. 窗口未知 / 比例关掉时**不**做压力压缩（回退按条数，行为与改造前一致）；
2. 压力越线时才压，且保留条数按 `keep_ratio` 收紧（不超过 `history_rounds`）；
3. `prune` 后端**不调模型**，只替换超长工具结果，对话文本与配对结构不动；
4. `summary` 后端失败时原样返回（压缩是优化）；
5. 压缩写 `compaction/start|summary|end`，失败路径也有 `end`（不留半截括号）。
"""

from __future__ import annotations

from app.agent.assembly import compaction
from app.agent.assembly.compress import maybe_compress_context, split_history
from app.agent.assembly.context_budget import (
    context_window_of,
    keep_count_for,
    should_compress_by_pressure,
    usage_ratio,
)


class _FakeLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def append(self, type_: str, **payload):
        self.events.append((type_, payload))
        return {"seq": len(self.events), "type": type_, "payload": payload}


def _history(n: int, *, tool_chars: int = 0) -> list[dict]:
    """n 轮；``tool_chars`` > 0 时每轮带一对合法的 assistant(tool_calls)+tool。"""
    out: list[dict] = []
    for i in range(n):
        out.append({"role": "user", "content": f"用户 {i}"})
        if tool_chars:
            out.append({
                "role": "assistant", "content": None,
                "tool_calls": [{
                    "id": f"c{i}", "type": "function",
                    "function": {"name": "probe", "arguments": "{}"},
                }],
            })
            out.append({"role": "tool", "tool_call_id": f"c{i}", "content": "x" * tool_chars})
        else:
            out.append({"role": "assistant", "content": f"回复 {i}"})
    return out


# ── 预算 ──────────────────────────────────────────────


def test_window_unknown_disables_pressure():
    config = {"context_window": 0, "context_compress_trigger_ratio": 0.8}

    assert context_window_of(config) == 0
    assert should_compress_by_pressure(_history(50), config) is False


def test_pressure_triggers_only_above_the_ratio():
    history = _history(20)          # 约 40 条短消息
    estimated = usage_ratio(history, 100000)
    config = {"context_window": 100000, "context_compress_trigger_ratio": estimated + 0.01}

    assert should_compress_by_pressure(history, config) is False
    assert should_compress_by_pressure(history, {**config, "context_compress_trigger_ratio": 0.0}) is False
    assert should_compress_by_pressure(history, {**config, "context_compress_trigger_ratio": 0.0001}) is True


def test_keep_count_respects_ratio_and_the_configured_upper_bound():
    history = _history(50)

    assert keep_count_for(history, {"context_compress_keep_ratio": 0.16}, 50) == min(
        max(2, int(len(history) * 0.16)), 50
    )
    assert keep_count_for(history, {"context_compress_keep_ratio": 0.5}, 10) == 10, "不超过 history_rounds"
    assert keep_count_for(history, {}, 12) == 12, "比例关掉时就是旧口径"


# ── pruner ────────────────────────────────────────────


def test_pruner_only_replaces_long_tool_results():
    history = _history(3, tool_chars=3000)

    pruned, count = compaction.prune_old_tool_results(history, min_chars=1000)

    assert count == 3
    assert all(m["role"] != "tool" or "已省略" in m["content"] for m in pruned)
    assert [m["content"] for m in history if m["role"] == "user"] == [
        m["content"] for m in pruned if m["role"] == "user"
    ], "对话文本一个字都不能改"


def test_pruner_keeps_short_results():
    history = _history(2, tool_chars=50)

    pruned, count = compaction.prune_old_tool_results(history, min_chars=1000)

    assert count == 0 and pruned == history


# ── 后端 ──────────────────────────────────────────────


async def test_prune_backend_never_calls_the_model(monkeypatch):
    async def _boom(*args, **kwargs):
        raise AssertionError("prune 后端不该调模型")

    monkeypatch.setattr("app.agent.assembly.compress.chat_with_fallback", _boom)
    backend = compaction.PruneBackend()
    history = _history(5, tool_chars=3000)
    old, keep = split_history(history, 4)

    result = await backend.compact(old, keep, plan=compaction.CompactionPlan(keep_tail=4))

    assert result.compacted and result.pruned > 0
    assert len(result.history) == len(history), "只替换内容，不改变条数"


async def test_summary_backend_falls_back_when_model_returns_nothing(monkeypatch):
    async def _empty(*args, **kwargs):
        return type("R", (), {"text": ""})()

    monkeypatch.setattr("app.agent.assembly.compress.chat_with_fallback", _empty)
    backend = compaction.SummaryBackend()
    history = _history(5)
    old, keep = split_history(history, 4)

    result = await backend.compact(old, keep, plan=compaction.CompactionPlan(keep_tail=4))

    assert result.compacted is False
    assert result.history == old + keep, "失败时原样返回（压缩是优化）"


async def test_both_backend_prunes_even_if_summary_fails(monkeypatch):
    async def _empty(*args, **kwargs):
        return type("R", (), {"text": ""})()

    monkeypatch.setattr("app.agent.assembly.compress.chat_with_fallback", _empty)
    backend = compaction.BothBackend()
    history = _history(5, tool_chars=3000)
    old, keep = split_history(history, 4)

    result = await backend.compact(old, keep, plan=compaction.CompactionPlan(keep_tail=4))

    assert result.pruned > 0, "摘要失败也要拿到裁剪这一半的收益"
    assert result.compacted is True


def test_backend_selection_falls_back_to_summary():
    assert compaction.choose_backend({}).name == "summary"
    assert compaction.choose_backend({"context_compress_backend": "prune"}).name == "prune"
    assert compaction.choose_backend({"context_compress_backend": "both"}).name == "both"
    assert compaction.choose_backend({"context_compress_backend": "nonsense"}).name == "summary"


# ── 入口 + 事件 ───────────────────────────────────────


async def test_pressure_compaction_writes_bracketed_events(monkeypatch):
    async def _empty(*args, **kwargs):
        return type("R", (), {"text": "摘要内容"})()

    monkeypatch.setattr("app.agent.assembly.compress.chat_with_fallback", _empty)
    log = _FakeLog()
    history = _history(20, tool_chars=3000)
    config = {
        "context_compress_enable": True,
        "history_rounds": 8,
        "context_window": 100,
        "context_compress_trigger_ratio": 0.001,
        "context_compress_keep_ratio": 0.1,
        "context_compress_backend": "both",
    }

    result = await maybe_compress_context([], config, history, 8, pressure=True, log=log)

    types = [t for t, _ in log.events]
    assert types[0] == "compaction/start"
    assert "compaction/summary" in types
    assert types[-1] == "compaction/end"
    assert result[0]["role"] == "system" and "摘要内容" in result[0]["content"]


async def test_no_compaction_writes_nothing(monkeypatch):
    log = _FakeLog()

    result = await maybe_compress_context(
        [], {"context_compress_enable": True}, _history(3), 50, pressure=False, log=log
    )

    assert result == _history(3)
    assert log.events == []


async def test_failed_summary_still_closes_the_bracket(monkeypatch):
    async def _boom(*args, **kwargs):
        raise RuntimeError("上游挂了")

    monkeypatch.setattr("app.agent.assembly.compress.chat_with_fallback", _boom)
    log = _FakeLog()

    await maybe_compress_context(
        [], {"context_compress_enable": True}, _history(20), 4, pressure=True, log=log
    )

    types = [t for t, _ in log.events]
    assert types[0] == "compaction/start" and types[-1] == "compaction/end"
    assert "compaction/summary" not in types, "没压成就不该记摘要"
