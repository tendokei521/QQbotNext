"""P0-1：被跳过的消息必须留下可查原因（去重 + 统一格式 + 可注入）。

背景：LLM 流水线里多处静默 return，用户表现为「群里 @ 没反应、日志干净」。
本测试锁定四件事：①用户可见级别同一 (账号, 原因) **进程内只记一次**
（"仅启动第一次体现"，重启后才再提醒）；②同一原因跨不同群也只提醒一次
（细节不参与去重）；③debug 级是技术日志、保留 60s 窗口；④日志含原因码/细节/修复提示，
流水线的场景开关确实走统一入口。
"""

from __future__ import annotations

from pathlib import Path

import app.core.skip_log as skip_log


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


def test_same_skip_is_logged_once_per_process(monkeypatch):
    """用户口径：这类提示只在（进程重启后的）第一次出现，之后同一情形不再刷屏。

    旧实现是 60s 窗口，于是同一群被禁用时每几分钟仍会冒一条 WARNING。
    """
    calls = _capture(monkeypatch)
    clock = [1000.0]
    monkeypatch.setattr(skip_log, "_now", lambda: clock[0])

    assert skip_log.log_skip("acc1", "group_disabled", "群 1075894179") is True
    for _ in range(3):
        clock[0] += 6 * 3600  # 远超旧的 60s 窗口：同一情形仍然不再记录
        assert skip_log.log_skip("acc1", "group_disabled", "群 1075894179") is False
    assert len(calls) == 1


def test_different_detail_is_not_reported_separately(monkeypatch):
    """细节不参与用户可见级别的去重：两个群都被禁用也只留一条警告。"""
    calls = _capture(monkeypatch)
    assert skip_log.log_skip("acc1", "group_disabled", "群 1") is True
    assert skip_log.log_skip("acc1", "group_disabled", "群 2") is False
    assert skip_log.log_skip("acc1", "group_disabled", "群 1") is False
    assert len(calls) == 1


def test_debug_level_keeps_repeat_window(monkeypatch):
    """debug 级只进 debug.log（技术证据），保留 60s 窗口，不参与"进程内一次"。"""
    calls = _capture(monkeypatch)
    clock = [500.0]
    monkeypatch.setattr(skip_log, "_now", lambda: clock[0])

    assert skip_log.log_skip("acc1", "trigger_not_matched", "group=1", level="debug") is True
    clock[0] += 10
    assert skip_log.log_skip("acc1", "trigger_not_matched", "group=1", level="debug") is False
    clock[0] += 120
    assert skip_log.log_skip("acc1", "trigger_not_matched", "group=1", level="debug") is True
    assert len(calls) == 2
    assert all(call["level"] == "debug" for call in calls)


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
    assert "from app.core.skip_log import log_skip" in src
    assert '"group_disabled"' in src
    assert '"private_disabled"' in src
    assert '"no_api_key"' in src
