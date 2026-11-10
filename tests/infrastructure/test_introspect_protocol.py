"""账号对外视图：官方账号必须带 protocol / capabilities / capability_notes。"""

from __future__ import annotations

from types import SimpleNamespace

from app.infrastructure.onebot.introspect import bot_info


def _conn(protocol: str) -> SimpleNamespace:
    return SimpleNamespace(
        ws_url="",
        account_id="acc1",
        account_name="官方号",
        bot_id="BOT_ID",
        owner_id=None,
        status="connected",
        login_info={"user_id": "BOT_ID", "nickname": "机器人"},
        reconnect_attempts=0,
        last_error=None,
        auto_connect=False,
        conn_mode="forward",
        protocol=protocol,
        transport="ws" if protocol == "qqbot" else "",
        app_id="APPID" if protocol == "qqbot" else "",
        suppress_auto_reconnect=False,
    )


def test_qqbot_info_exposes_capabilities():
    info = bot_info(_conn("qqbot"))
    assert info["protocol"] == "qqbot"
    assert info["transport"] == "ws"
    assert info["app_id"] == "APPID"
    assert info["capabilities"]["onebot_tools"] is False
    assert info["capabilities"]["group_list"] == "learned"
    assert any("openid" in note for note in info["capability_notes"])


def test_onebot_info_keeps_full_capabilities():
    info = bot_info(_conn("onebot"))
    assert info["protocol"] == "onebot"
    assert info["capabilities"]["onebot_tools"] is True
    assert info["capability_notes"] == []
