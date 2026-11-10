"""协议能力真源与 ID 归一的契约（跨层：领域层唯一真相）。"""

from __future__ import annotations

import pytest

from app.domain.ids import as_int_id, same_id
from app.domain.protocol import (
    PROTOCOL_ONEBOT,
    PROTOCOL_QQBOT,
    CapabilityUnavailableError,
    capability_view,
    normalize_protocol,
    supports_action,
)


def test_onebot_supports_any_action():
    assert supports_action(PROTOCOL_ONEBOT, "send_poke") is True
    assert supports_action(PROTOCOL_ONEBOT, "get_group_list") is True


def test_qqbot_only_supports_whitelisted_actions():
    assert supports_action(PROTOCOL_QQBOT, "send_group_msg") is True
    assert supports_action(PROTOCOL_QQBOT, "delete_msg") is True
    assert supports_action(PROTOCOL_QQBOT, "send_poke") is False
    assert supports_action(PROTOCOL_QQBOT, "") is False


def test_capability_view_marks_onebot_tools_unavailable_for_qqbot():
    view = capability_view(PROTOCOL_QQBOT)
    assert view["onebot_tools"] is False
    assert view["send_text"] is True
    assert view["group_list"] == "learned"
    assert capability_view(PROTOCOL_ONEBOT)["onebot_tools"] is True


def test_capability_error_carries_protocol_and_action():
    err = CapabilityUnavailableError(PROTOCOL_QQBOT, "send_poke")
    assert err.protocol == "qqbot"
    assert err.capability == "send_poke"
    assert "官方 QQ 机器人" in str(err)


def test_unknown_protocol_falls_back_to_onebot():
    assert normalize_protocol("mystery") == PROTOCOL_ONEBOT
    assert normalize_protocol(None) == PROTOCOL_ONEBOT


def test_ids_keep_openids_as_strings():
    assert as_int_id("123") == 123
    assert as_int_id("E4F4AEA33253A2797FB897C50B81D7ED") == "E4F4AEA33253A2797FB897C50B81D7ED"
    assert as_int_id(None) == ""
    assert same_id(1, "1") is True


@pytest.mark.parametrize("action", ["send_poke", "send_forward_msg", "get_group_member_list"])
def test_qqbot_unsupported_messages_are_readable(action: str):
    err = CapabilityUnavailableError(PROTOCOL_QQBOT, action)
    assert action in str(err)
