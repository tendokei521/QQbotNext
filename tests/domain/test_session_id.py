"""会话 id 解析/构造的唯一口径测试（`app/domain/session.py`）。"""

import pytest

from app.domain.session import (
    SessionIdError,
    make_session_id,
    parse_session_id,
    try_parse_session_id,
)


@pytest.mark.parametrize(
    ("session_id", "expected"),
    [
        ("group_123", (True, "123")),
        ("private_456", (False, "456")),
        ("group_0", (True, "0")),
        ("789", (False, "789")),
        ("  group_42  ", (True, "42")),
    ],
)
def test_parse_session_id(session_id, expected):
    assert parse_session_id(session_id) == expected


@pytest.mark.parametrize("bad", ["", "   ", None])
def test_parse_session_id_rejects_empty(bad):
    with pytest.raises(SessionIdError):
        parse_session_id(bad)


def test_try_parse_returns_none_instead_of_raising():
    assert try_parse_session_id("") is None
    assert try_parse_session_id("group_1") == (True, "1")


def test_make_session_id_round_trips():
    assert make_session_id(True, 123) == "group_123"
    assert make_session_id(False, 456) == "private_456"
    assert parse_session_id(make_session_id(True, 123)) == (True, "123")
