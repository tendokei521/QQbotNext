"""官方协议约束契约：msg_id 去重 + 被动回复窗口（msg_id/msg_seq）。"""

from __future__ import annotations

import pytest

from app.infrastructure.qqbot.connection import QqBotConnection
from app.infrastructure.qqbot.inbox import Inbox


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_dedupe_ttl():
    clock = Clock()
    inbox = Inbox(dedupe_ttl=10, clock=clock)
    assert inbox.is_duplicate("m1") is False
    assert inbox.is_duplicate("m1") is True
    clock.now += 11
    assert inbox.is_duplicate("m1") is False


def test_group_passive_window_caps_at_five():
    clock = Clock()
    inbox = Inbox(clock=clock)
    inbox.remember_inbound("group", "G", "M")
    seqs = [inbox.next_passive("group", "G") for _ in range(5)]
    assert [seq for _, seq in seqs] == [1, 2, 3, 4, 5]
    assert inbox.next_passive("group", "G") is None


def test_group_passive_window_expires_after_five_minutes():
    clock = Clock()
    inbox = Inbox(clock=clock)
    inbox.remember_inbound("group", "G", "M")
    clock.now += 301
    assert inbox.next_passive("group", "G") is None


def test_private_window_allows_four_replies_in_sixty_minutes():
    clock = Clock()
    inbox = Inbox(clock=clock)
    inbox.remember_inbound("private", "U", "M")
    clock.now += 3500
    assert [inbox.next_passive("private", "U")[1] for _ in range(4)] == [1, 2, 3, 4]
    assert inbox.next_passive("private", "U") is None


class FakeAPI:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    async def send_group_message(self, target: str, payload: dict) -> dict:
        self.calls.append(("group", target, dict(payload)))
        return {"id": f"OUT{len(self.calls)}"}

    async def send_c2c_message(self, target: str, payload: dict) -> dict:
        self.calls.append(("private", target, dict(payload)))
        return {"id": f"OUT{len(self.calls)}"}

    async def delete_group_message(self, target: str, message_id: str) -> dict:
        return {}

    async def delete_c2c_message(self, target: str, message_id: str) -> dict:
        return {}


class FakeUploader:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.seen: list[str] = []

    async def resolve(self, scene: str, target: str, segment) -> str:
        if self.fail:
            from app.infrastructure.qqbot.media import MediaError

            raise MediaError("上传失败（假）")
        self.seen.append(str(getattr(segment, "type", "")))
        return "FILEINFO"


def _conn() -> QqBotConnection:
    conn = QqBotConnection(account_id="bot1", account_name="bot1", app_id="a", app_secret="s")
    conn.api = FakeAPI()  # type: ignore[assignment]
    conn.uploader = FakeUploader()  # type: ignore[assignment]
    return conn


@pytest.mark.asyncio
async def test_send_attaches_passive_msg_id_and_increments_seq():
    conn = _conn()
    conn.inbox.remember_inbound("group", "G", "M1")
    r1 = await conn.send_group_msg("G", "hello")
    r2 = await conn.send_group_msg("G", "again")
    assert r1["status"] == "ok" and r2["status"] == "ok"
    first = conn.api.calls[0][2]
    second = conn.api.calls[1][2]
    assert (first["msg_id"], first["msg_seq"]) == ("M1", 1)
    assert (second["msg_id"], second["msg_seq"]) == ("M1", 2)


@pytest.mark.asyncio
async def test_send_without_passive_window_fails_clearly():
    conn = _conn()
    response = await conn.send_group_msg("G", "hello")
    assert response["status"] == "failed"
    assert response["retcode"] == "NO_PASSIVE_WINDOW"
    assert conn.api.calls == []


@pytest.mark.asyncio
async def test_media_message_uses_msg_type_7_with_file_info():
    conn = _conn()
    conn.inbox.remember_inbound("group", "G", "M1")
    response = await conn.send_group_msg("G", [{"type": "image", "data": {"file": "x.png"}}])
    assert response["status"] == "ok"
    payload = conn.api.calls[0][2]
    assert payload["msg_type"] == 7
    assert payload["media"]["file_info"] == "FILEINFO"
    assert payload["content"] == " "  # 群聊要求 content 非空
    assert (payload["msg_id"], payload["msg_seq"]) == ("M1", 1)


@pytest.mark.asyncio
async def test_media_with_caption_keeps_text_on_first_message():
    conn = _conn()
    conn.inbox.remember_inbound("group", "G", "M1")
    await conn.send_group_msg("G", [
        {"type": "text", "data": {"text": "看图"}},
        {"type": "image", "data": {"file": "x.png"}},
    ])
    payload = conn.api.calls[0][2]
    assert payload["content"] == "看图"
    assert payload["msg_type"] == 7


@pytest.mark.asyncio
async def test_media_upload_failure_is_reported():
    conn = _conn()
    conn.uploader = FakeUploader(fail=True)  # type: ignore[assignment]
    conn.inbox.remember_inbound("group", "G", "M1")
    response = await conn.send_group_msg("G", [{"type": "image", "data": {"file": "x.png"}}])
    assert response["status"] == "failed"
    assert response["retcode"] == "MEDIA_UPLOAD_FAILED"


@pytest.mark.asyncio
async def test_active_message_respects_daily_quota():
    conn = _conn()
    conn.allow_active_message = True
    conn.active_daily_limit = 1
    first = await conn.send_group_msg("G", "主动一条")
    assert first["status"] == "ok"
    payload = conn.api.calls[0][2]
    assert "msg_id" not in payload, "窗口外发送不应带被动凭据"

    second = await conn.send_group_msg("G", "再来一条")
    assert second["status"] == "failed"
    assert second["retcode"] == "ACTIVE_QUOTA_EXCEEDED"


@pytest.mark.asyncio
async def test_active_message_disabled_by_default():
    conn = _conn()
    response = await conn.send_group_msg("G", "hi")
    assert response["retcode"] == "NO_PASSIVE_WINDOW"


@pytest.mark.asyncio
async def test_group_list_comes_from_learned_events():
    conn = _conn()
    conn.known_groups_provider = None
    conn.learn_group("G1")
    response = await conn.get_group_list()
    assert response["status"] == "ok"
    assert response["data"][0]["group_id"] == "G1"


def test_unsupported_onebot_action_raises_capability_error():
    conn = _conn()
    with pytest.raises(Exception) as exc:
        import asyncio

        asyncio.run(conn.send_poke("U"))
    assert "官方 QQ 机器人" in str(exc.value)
