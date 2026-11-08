"""会话历史写入的失败留痕契约：``add_message`` 丢条必须可排查。

2026-11-08 事故复盘延伸出的静默失败点之一：会话过期/被回收/换号后，
``SessionManager.add_message`` 直接跳过整个函数体——用户消息或机器人发言从历史里消失，
下一轮上下文断片（表现为"模型不记得刚才说的话"），而日志里一片干净。

本用例锁的是**对外行为**：返回 False（调用方可判断）+ 留一条带会话/角色/账号的
``history.add_message_dropped``；不锁日志文案全文。
"""

from __future__ import annotations

import pytest

from app.core import diagnostics


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, log, code, message="", **kwargs):  # noqa: ANN001
        self.calls.append({"code": code, "message": message, **kwargs})
        return True

    def codes(self) -> list[str]:
        return [call["code"] for call in self.calls]

    def first(self, code: str) -> dict:
        for call in self.calls:
            if call["code"] == code:
                return call
        raise AssertionError(f"没有记录到 {code}：{self.codes()}")


@pytest.fixture()
def recorder(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(diagnostics, "log_failure", recorder)
    yield recorder
    diagnostics.reset_failure_state()


def test_add_message_on_missing_session_returns_false_and_logs(recorder):
    from app.agent.session.manager import SessionManager

    mgr = SessionManager("contract_bot_drop")
    try:
        assert mgr.add_message("group_missing", "user", "在吗", user_id="1") is False
        fields = recorder.first("history.add_message_dropped")["fields"]
        assert fields["session"] == "group_missing"
        assert fields["role"] == "user"
        assert fields["bot"] == "contract_bot_drop"
    finally:
        SessionManager.evict("contract_bot_drop")


def test_add_message_on_live_session_returns_true(recorder):
    from app.agent.session.manager import SessionManager

    mgr = SessionManager("contract_bot_ok")
    try:
        mgr.create_session("group_live", "group")
        assert mgr.add_message("group_live", "user", "在吗", user_id="1") is True
        assert "history.add_message_dropped" not in recorder.codes()
    finally:
        SessionManager.evict("contract_bot_ok")
