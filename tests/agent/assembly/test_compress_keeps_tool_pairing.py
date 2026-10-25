"""压缩时保持工具配对（P5 第 4 步）。

构造含 `assistant(tool_calls)` + `role:tool` 的历史，断言**任何**切点都不会把一对切开：

- 正常历史：切点行为与改造前一致（不多挪、不少挪）；
- 切点正好落在配对中间：往前挪一位，旧段/保留段都合法；
- 整段都是工具消息：最坏挪到 0（不压缩），而不是发出非法请求。
"""

from __future__ import annotations

from app.agent.assembly.compress import split_history
from app.agent.assembly.pairing import legal_cut, pairing_is_closed


def _pair(call_id: str = "c0", result: str = "工具结果") -> list[dict]:
    return [
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": "probe", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": call_id, "content": result},
    ]


def _pairing_ok(segment: list[dict], *, label: str) -> None:
    """一个片段自己必须是闭合的（不能以 tool 结果开头 / 以 tool_calls 结尾）。"""
    calls = sum(1 for m in segment if m.get("role") == "assistant" and m.get("tool_calls"))
    results = sum(1 for m in segment if m.get("role") == "tool")
    assert calls == results, f"{label}: 配对不闭合 (calls={calls}, results={results})"


def test_plain_history_cut_is_unchanged():
    history = [{"role": "user", "content": f"m{i}"} for i in range(10)]

    old, keep = split_history(history, 3)

    assert len(keep) == 3 and len(old) == 7
    assert keep[0]["content"] == "m7"


def test_cut_inside_a_pair_moves_back():
    # 前面 6 条普通消息 + 一对工具消息 + 2 条普通消息；keep=3 时切点落在配对中间
    history = (
        [{"role": "user", "content": f"m{i}"} for i in range(6)]
        + _pair()
        + [{"role": "assistant", "content": "收尾"}, {"role": "user", "content": "再问"}]
    )

    old, keep = split_history(history, 3)

    _pairing_ok(old, label="旧段")
    _pairing_ok(keep, label="保留段")
    assert keep[-1]["content"] == "再问"
    assert len(keep) >= 3, "向前挪切点只会让保留段变长"


def test_legal_cut_returns_zero_when_no_legal_position_exists():
    history = _pair()

    assert legal_cut(history, 1) == 0, "宁可不压缩，也不发出非法请求"


def test_pairing_is_closed_checks_the_boundary_only():
    history = [{"role": "user", "content": "问"}] + _pair()

    assert pairing_is_closed(history, 0)
    assert not pairing_is_closed(history, 2), "切在 assistant(tool_calls) 之后、tool 之前"
    assert pairing_is_closed(history, 3)


def test_split_history_is_a_noop_when_nothing_exceeds_the_window():
    history = _pair()

    assert split_history(history, 10) == ([], history)
