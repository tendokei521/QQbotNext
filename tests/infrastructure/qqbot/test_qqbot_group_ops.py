"""官方群管理接口映射：禁言 / 踢人 / 群信息 / 成员列表（含 11253 降级）。"""

from __future__ import annotations

import pytest

from app.infrastructure.qqbot.api import QqBotAPIError
from app.infrastructure.qqbot.connection import QqBotConnection


class FakeGroupAPI:
    def __init__(self, *, fail_code: int | None = None) -> None:
        self.fail_code = fail_code
        self.restrict_calls: list[tuple[str, list[dict]]] = []
        self.remove_calls: list[tuple[str, list[str], bool]] = []
        self.members_pages = [
            {"members": [
                {"member_openid": "U1", "username": "小明", "member_role": "member"},
                {"member_openid": "U2", "username": "小红", "member_role": "admin"},
            ], "next_cursor": ""},
        ]

    def _maybe_fail(self) -> None:
        if self.fail_code is not None:
            raise QqBotAPIError(self.fail_code, "应用无接口访问权限")

    async def group_info(self, group_openid: str) -> dict:
        self._maybe_fail()
        return {"group_openid": group_openid, "group_name": "测试群", "group_member_num": 12}

    async def group_members(self, group_openid: str, cursor: str = "") -> dict:
        self._maybe_fail()
        return self.members_pages[0]

    async def restrict_chat(self, group_openid: str, members: list[dict]) -> dict:
        self._maybe_fail()
        self.restrict_calls.append((group_openid, members))
        return {}

    async def batch_remove_members(self, group_openid: str, openids: list[str], *, blacklist: bool = False) -> dict:
        self._maybe_fail()
        self.remove_calls.append((group_openid, list(openids), blacklist))
        return {"remove_members_result": "success"}


def _conn(api: FakeGroupAPI) -> QqBotConnection:
    conn = QqBotConnection(account_id="bot1", app_id="a", app_secret="s")
    conn.api = api  # type: ignore[assignment]
    return conn


@pytest.mark.asyncio
async def test_get_group_info_returns_name_and_count():
    conn = _conn(FakeGroupAPI())
    response = await conn.get_group_info("G1")
    assert response["status"] == "ok"
    assert response["data"]["group_name"] == "测试群"
    assert response["data"]["member_count"] == 12


@pytest.mark.asyncio
async def test_get_group_member_list_maps_openid_and_role():
    conn = _conn(FakeGroupAPI())
    response = await conn.get_group_member_list("G1")
    assert response["status"] == "ok"
    assert response["data"][0]["user_id"] == "U1"
    assert response["data"][1]["role"] == "admin"


@pytest.mark.asyncio
async def test_set_group_ban_builds_rfc3339_expiry():
    api = FakeGroupAPI()
    conn = _conn(api)
    response = await conn.set_group_ban("G1", "U1", duration=600)
    assert response["status"] == "ok"
    group_id, members = api.restrict_calls[0]
    assert group_id == "G1"
    assert members[0]["op"] == "add"
    assert members[0]["member_openid"] == "U1"
    assert members[0]["mute_expire_at"].endswith("+00:00")


@pytest.mark.asyncio
async def test_set_group_kick_uses_batch_remove():
    api = FakeGroupAPI()
    conn = _conn(api)
    response = await conn.set_group_kick("G1", "U1", reject_add_request=True)
    assert response["status"] == "ok"
    assert api.remove_calls == [("G1", ["U1"], True)]


@pytest.mark.asyncio
async def test_permission_denied_is_returned_not_raised():
    conn = _conn(FakeGroupAPI(fail_code=11253))
    response = await conn.get_group_member_list("G1")
    assert response["status"] == "failed"
    assert response["retcode"] == 11253
    assert "11253" not in str(response["message"]) or "权限" in str(response["message"])
