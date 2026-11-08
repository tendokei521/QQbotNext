"""``skip_log`` 去重键契约：detail 里的易变数字不能击败 ``interval_s`` 限流。

线上实证：``[skip] reason=participation_budget`` 平均每 15~40 秒刷一条，而调用点写的是
``interval_s=120``。原因是 detail 里拼着"已用 12/50 次、621409/1000000 tokens"这类
每次都在变的数字，而限流键恰好是 ``(账号, reason, detail)`` → 每轮都是新 key。

修法：``log_skip`` 支持显式 ``dedupe_key``（稳定键）；给了它就只用它组键，detail 照常打印。
"""

from __future__ import annotations

from app.core import skip_log


def test_volatile_detail_breaks_interval_without_dedupe_key(monkeypatch):
    emitted: list[str] = []
    monkeypatch.setattr(skip_log, "_emit", lambda account, message, level: emitted.append(message))
    monkeypatch.setattr(skip_log, "_now", lambda: 1000.0)
    skip_log.reset_skip_log_state()
    try:
        assert skip_log.log_skip("acc", "budget", "已用 1/50 次", interval_s=120) is True
        # 旧语义：detail 不同 → 不同 key → 窗口形同虚设（这里锁的是"没有 dedupe_key 就不限流"）
        assert skip_log.log_skip("acc", "budget", "已用 2/50 次", interval_s=120) is True
        assert len(emitted) == 2
    finally:
        skip_log.reset_skip_log_state()


def test_stable_dedupe_key_keeps_details_but_throttles(monkeypatch):
    emitted: list[str] = []
    monkeypatch.setattr(skip_log, "_emit", lambda account, message, level: emitted.append(message))
    monkeypatch.setattr(skip_log, "_now", lambda: 1000.0)
    skip_log.reset_skip_log_state()
    try:
        assert (
            skip_log.log_skip(
                "acc",
                "participation_budget",
                "已用 1/50 次",
                interval_s=120,
                dedupe_key="participation_budget",
            )
            is True
        )
        assert (
            skip_log.log_skip(
                "acc",
                "participation_budget",
                "已用 2/50 次",
                interval_s=120,
                dedupe_key="participation_budget",
            )
            is False
        )
        assert len(emitted) == 1
    finally:
        skip_log.reset_skip_log_state()


def test_window_expiry_lets_it_through_again(monkeypatch):
    clock = {"now": 1000.0}
    emitted: list[str] = []
    monkeypatch.setattr(skip_log, "_emit", lambda account, message, level: emitted.append(message))
    monkeypatch.setattr(skip_log, "_now", lambda: clock["now"])
    skip_log.reset_skip_log_state()
    try:
        assert skip_log.log_skip("acc", "budget", "x", interval_s=120, dedupe_key="k") is True
        clock["now"] += 121
        assert skip_log.log_skip("acc", "budget", "y", interval_s=120, dedupe_key="k") is True
        assert len(emitted) == 2
    finally:
        skip_log.reset_skip_log_state()
