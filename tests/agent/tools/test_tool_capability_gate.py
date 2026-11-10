"""协议能力闸门：官方 QQ 机器人账号上的 OneBot 专属工具必须判为不可用。

契约（不是实现快照）：
- 同一工具在 OneBot 账号上可用、在官方账号上不可用；
- 不可用时必须带 ``capability_missing`` 与可读 ``reason_text``（含协议名与动作名）；
- 未连接（``runtime._bot is None``）也要按账号配置判出协议，不能误判成 OneBot。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.agent.tools.state import (
    REASON_CAPABILITY_MISSING,
    REASON_OK,
    resolve_tool_state,
)


class FakeConfig:
    def __init__(self, values: dict | None = None) -> None:
        self.values = dict(values or {"onebot_tools_enable": True})

    def get(self, key, default=None):
        return self.values.get(key, default)

    def enabled(self):
        return True


class FakeConfigService:
    def __init__(self, protocol: str) -> None:
        self.protocol = protocol

    def get_bot(self, account_id: str):
        return {"id": account_id, "name": account_id, "protocol": self.protocol}


def _runtime(protocol: str, *, connected_bot=None) -> SimpleNamespace:
    return SimpleNamespace(
        bot_id="bot1",
        config=FakeConfig(),
        config_service=FakeConfigService(protocol),
        _bot=connected_bot,
    )


def test_onebot_account_keeps_all_tools():
    state = resolve_tool_state(_runtime("onebot"), name="send_poke", source="onebot")
    assert state.available is True
    assert state.reason == REASON_OK


def test_qqbot_account_hides_onebot_only_tool_offline():
    """未连接时也要按配置判成官方账号（runtime._bot 为 None）。"""
    runtime = _runtime("qqbot")
    state = resolve_tool_state(runtime, name="send_poke", source="onebot")
    assert state.available is False
    assert state.reason == REASON_CAPABILITY_MISSING
    assert "官方 QQ 机器人" in state.reason_text
    assert "send_poke" in state.reason_text
    assert state.extra.get("capability") == "send_poke"
    assert state.extra.get("protocol") == "qqbot"


def test_qqbot_account_keeps_supported_onebot_action():
    """官方账号仍要放行真正支持的发送类动作（文本收发）。"""
    state = resolve_tool_state(_runtime("qqbot"), name="send_group_msg", source="onebot")
    assert state.available is True


def test_connected_bot_protocol_wins_over_config():
    """已连接时以连接对象的协议为准（换号/热切换窗口配置可能滞后）。"""
    bot = SimpleNamespace(protocol="onebot")
    state = resolve_tool_state(_runtime("qqbot", connected_bot=bot), name="send_poke", source="onebot")
    assert state.available is True
