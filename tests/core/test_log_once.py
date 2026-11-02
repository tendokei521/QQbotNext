"""配置类告警的「进程内只提醒一次」（``app/core/log_once.py``）。

背景：没选模型、引用的模型被删、不在允许清单里……是**配置状态**，不是瞬时事件；
每条消息刷一遍 WARNING 只会把真问题淹掉。这里锁定：

1. 同一 ``(账号, key)`` 只写一次，文案与级别不变；
2. 不同 key / 不同账号互不影响（不是"全局只留一条"）；
3. ``reset_log_once_state()`` 后可以再提醒（重启/配置变更场景）。
"""

from __future__ import annotations

import pytest

import app.core.log_once as log_once


class _Log:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    def add_info(self, _label):  # noqa: ANN001, ANN202
        return self

    def warning(self, message: str) -> None:
        self.lines.append(("warning", str(message)))

    def info(self, message: str) -> None:
        self.lines.append(("info", str(message)))

    def error(self, message: str) -> None:
        self.lines.append(("error", str(message)))


@pytest.fixture()
def captured(monkeypatch):
    log = _Log()
    monkeypatch.setattr(log_once, "logger", log)
    log_once.reset_log_once_state()
    return log


def test_same_account_and_key_is_reported_once(captured):
    assert log_once.warn_once("acc1", "provider.no_model", "没有可用模型") is True
    assert log_once.warn_once("acc1", "provider.no_model", "没有可用模型") is False
    assert log_once.warn_once("acc1", "provider.no_model", "换了文案也还是同一件事") is False
    assert captured.lines == [("warning", "没有可用模型")]


def test_different_key_or_account_is_reported_separately(captured):
    log_once.warn_once("acc1", "provider.no_model", "a")
    log_once.warn_once("acc1", "provider.pool_denied", "b")
    log_once.warn_once("acc2", "provider.no_model", "c")
    assert len(captured.lines) == 3


def test_level_is_respected(captured):
    log_once.warn_once("acc1", "k", "出事了", level="error")
    assert captured.lines == [("error", "出事了")]


def test_empty_account_falls_back_to_placeholder(captured):
    log_once.warn_once(None, "k", "x")
    log_once.warn_once("", "k", "x")
    assert len(captured.lines) == 1, "空账号应归为同一个 ? 桶"


def test_reset_allows_reporting_again(captured):
    log_once.warn_once("acc1", "k", "x")
    log_once.reset_log_once_state()
    log_once.warn_once("acc1", "k", "x")
    assert len(captured.lines) == 2
