"""官方网关的「收到消息」日志契约：与 OneBot 同一套渲染口径。"""

from __future__ import annotations

from types import SimpleNamespace

from app.infrastructure.qqbot.codec import decode_event
from app.infrastructure.qqbot.gateway import QqBotGateway


class FakeLog:
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.records: list[tuple[str, str]] = []
        self._prefix = ""

    def prefix(self, name: str) -> FakeLog:
        self._prefix = name
        return self

    def info(self, text: str) -> None:
        self.parts.append(text)
        self.records.append((self._prefix, text))

    def debug(self, text: str) -> None:  # pragma: no cover
        self.parts.append(text)


def _conn() -> SimpleNamespace:
    return SimpleNamespace(
        account_id="official",
        account_name="官方号",
        learned_groups={"G1": "测试群"},
    )


def _bot() -> SimpleNamespace:
    return SimpleNamespace(protocol="qqbot", account_id="official", bot_id="BOT_OPENID")


def _event(payload: dict):
    return decode_event("GROUP_AT_MESSAGE_CREATE", payload, _bot())


def test_group_message_is_logged_with_group_name_and_sender():
    gateway = QqBotGateway()
    log = FakeLog()
    gateway.log = log
    event = _event({
        "id": "M1",
        "author": {"member_openid": "U1", "username": "小明", "member_role": "member"},
        "content": " 你好",
        "group_openid": "G1",
        "timestamp": "2026-11-13T10:00:00+08:00",
    })
    gateway._log_received(_conn(), event)

    assert log.records, "官方群消息没有写接收日志"
    prefix, text = log.records[0]
    assert "接收" in prefix
    assert "测试群(G1)" in prefix
    assert "小明(U1)" in text
    assert "你好" in text


def test_private_message_is_logged_without_group():
    gateway = QqBotGateway()
    log = FakeLog()
    gateway.log = log
    payload = {
        "id": "M2",
        "author": {"user_openid": "U2", "username": "小红"},
        "content": "在吗",
        "timestamp": "2026-11-13T10:00:00+08:00",
    }
    event = decode_event("C2C_MESSAGE_CREATE", payload, _bot())
    gateway._log_received(_conn(), event)

    prefix, text = log.records[0]
    assert "接收" in prefix
    assert "G1" not in prefix
    assert "小红(U2)" in text and "在吗" in text


def test_log_render_failure_does_not_raise():
    gateway = QqBotGateway()
    gateway.log = FakeLog()
    gateway._log_received(_conn(), object())  # 非事件对象：不能炸收消息主流程
