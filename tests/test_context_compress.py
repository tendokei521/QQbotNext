"""上下文压缩策略测试。"""

from app.llm.compress import (
    build_summary_messages,
    should_compress,
    split_history,
)


def _history(n: int) -> list[dict]:
    return [
        {"role": "assistant" if i % 2 else "user", "content": f"msg-{i}"}
        for i in range(n)
    ]


def test_should_compress_below_threshold():
    cfg = {"context_compress_enable": True, "context_compress_threshold": 0.75}
    # 50 * 0.75 = 37.5，38 条才触发
    assert not should_compress(_history(37), 50, cfg)
    assert should_compress(_history(38), 50, cfg)


def test_should_compress_disabled():
    cfg = {"context_compress_enable": False, "context_compress_threshold": 0.75}
    assert not should_compress(_history(100), 50, cfg)


def test_should_compress_invalid_threshold_falls_back():
    cfg = {"context_compress_enable": True, "context_compress_threshold": 2}
    # 无效阈值回退 0.75
    assert should_compress(_history(38), 50, cfg)


def test_split_history_keeps_ratio():
    history = _history(100)
    old, recent = split_history(history, 0.25)
    assert len(recent) == 25
    assert len(old) == 75
    assert recent[0] == history[-25]
    assert old[-1] == history[-26]


def test_split_history_keeps_at_least_one():
    history = _history(1)
    old, recent = split_history(history, 0.25)
    assert old == []
    assert recent == history


def test_build_summary_messages_appends_ack_when_last_user():
    history = _history(3)  # user, assistant, user
    messages = build_summary_messages(history, "请总结")
    assert messages[-1] == {"role": "user", "content": "请总结"}
    assert messages[-2] == {"role": "assistant", "content": "Acknowledged."}
    assert messages[0]["role"] == "system"
