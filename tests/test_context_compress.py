"""上下文压缩策略测试：只压缩超出 history_rounds 的部分。"""

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


def test_should_compress_when_exceeds_rounds():
    cfg = {"context_compress_enable": True}
    assert not should_compress(_history(50), 50, cfg)
    assert should_compress(_history(51), 50, cfg)


def test_should_compress_disabled():
    cfg = {"context_compress_enable": False}
    assert not should_compress(_history(100), 50, cfg)


def test_split_history_only_splits_excess():
    history = _history(60)
    old, recent = split_history(history, 50)
    assert len(recent) == 50
    assert len(old) == 10
    assert recent[0] == history[-50]
    assert old[-1] == history[-51]


def test_split_history_keeps_all_when_within_rounds():
    history = _history(30)
    old, recent = split_history(history, 50)
    assert old == []
    assert recent == history


def test_split_history_keeps_at_least_recent_when_zero_rounds():
    history = _history(3)
    old, recent = split_history(history, 0)
    assert old == history
    assert recent == []


def test_build_summary_messages_appends_ack_when_last_user():
    history = _history(3)  # user, assistant, user
    messages = build_summary_messages(history, "请总结")
    assert messages[-1] == {"role": "user", "content": "请总结"}
    assert messages[-2] == {"role": "assistant", "content": "Acknowledged."}
    assert messages[0]["role"] == "system"
