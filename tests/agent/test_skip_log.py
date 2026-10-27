"""P0-1：被跳过的消息必须留下可查原因（限流 + 统一格式 + 可注入）。

背景：LLM 流水线里多处静默 return，用户表现为「群里 @ 没反应、日志干净」。
本测试锁定三件事：①同一 (账号, 原因) 限流；②日志含原因码/细节/修复提示；
③流水线的场景开关确实走统一入口。
"""

from __future__ import annotations

from pathlib import Path

import app.agent.loop.skip_log as skip_log


def _capture(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    def fake_emit(account_id: str, message: str, level: str) -> None:
        calls.append({"account": account_id, "message": message, "level": level})

    monkeypatch.setattr(skip_log, "_emit", fake_emit)
    skip_log.reset_skip_log_state()
    return calls


def test_same_reason_is_throttled(monkeypatch):
    calls = _capture(monkeypatch)
    assert skip_log.log_skip("acc1", "group_disabled", "group=1", hint="去开群聊回复") is True
    assert skip_log.log_skip("acc1", "group_disabled", "group=1", hint="去开群聊回复") is False
    assert len(calls) == 1
    assert calls[0]["account"] == "acc1"
    assert calls[0]["level"] == "warning"
    message = calls[0]["message"]
    assert "reason=group_disabled" in message
    assert "group=1" in message
    assert "去开群聊回复" in message


def test_different_reason_or_account_is_not_throttled(monkeypatch):
    calls = _capture(monkeypatch)
    skip_log.log_skip("acc1", "group_disabled")
    skip_log.log_skip("acc1", "private_disabled")
    skip_log.log_skip("acc2", "group_disabled")
    assert len(calls) == 3


def test_interval_zero_disables_throttle(monkeypatch):
    calls = _capture(monkeypatch)
    for _ in range(3):
        skip_log.log_skip("acc1", "no_api_key", interval_s=0)
    assert len(calls) == 3


def test_reset_clears_throttle_state(monkeypatch):
    calls = _capture(monkeypatch)
    skip_log.log_skip("acc1", "group_disabled")
    skip_log.reset_skip_log_state()
    assert skip_log.log_skip("acc1", "group_disabled") is True
    assert len(calls) == 2


def test_emit_goes_through_account_scoped_logger(monkeypatch):
    records: list[tuple[str, str]] = []

    class _FakeLog:
        def warning(self, message: str) -> None:
            records.append(("warning", message))

        def info(self, message: str) -> None:
            records.append(("info", message))

    class _FakeLogger:
        def add_info(self, label: str) -> _FakeLog:
            records.append(("label", label))
            return _FakeLog()

    monkeypatch.setattr(skip_log, "logger", _FakeLogger())
    monkeypatch.setattr(skip_log, "account_label", lambda account_id: f"账号{account_id}")
    skip_log.reset_skip_log_state()

    assert skip_log.log_skip("acc9", "no_api_key", hint="去填 key", interval_s=0) is True
    assert records[0] == ("label", "账号acc9")
    assert records[1] == ("warning", "[skip] reason=no_api_key · 去填 key")


def test_pipeline_scene_switches_use_skip_log():
    """静态守卫：场景开关的 return 前必须走统一入口（防回退成静默 return）。"""
    src = Path("app/agent/loop/pipeline.py").read_text(encoding="utf-8")
    assert "from app.agent.loop.skip_log import log_skip" in src
    assert '"group_disabled"' in src
    assert '"private_disabled"' in src
    assert '"no_api_key"' in src
