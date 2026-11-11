"""网关群学习契约：学到的群写入 known_groups，并尽量补全群名（失败不反复重试）。"""

from __future__ import annotations

import pytest

from app.infrastructure.qqbot.gateway import QqBotGateway


class FakeConn:
    account_id = "official"
    learned_groups: dict[str, str] = {}

    def __init__(self, *, name: str = "测试群", fail: bool = False) -> None:
        self.name = name
        self.fail = fail
        self.info_calls = 0
        self.learned_groups = {}

    async def get_group_info(self, group_id: str) -> dict:
        self.info_calls += 1
        if self.fail:
            return {"status": "failed", "retcode": 40012010, "message": "应用无接口访问权限"}
        self.learned_groups[str(group_id)] = self.name
        return {"status": "ok", "data": {"group_id": group_id, "group_name": self.name}}


class FakeConfigService:
    def __init__(self) -> None:
        self.upserts: list[list[dict]] = []

    async def upsert_known_groups(self, account_id: str, groups: list[dict]) -> int:
        self.upserts.append([dict(g) for g in groups])
        return len(groups)


@pytest.mark.asyncio
async def test_remember_group_fills_name_once():
    gateway = QqBotGateway()
    gateway.config_service = FakeConfigService()
    conn = FakeConn()

    await gateway._remember_group(conn, "G1")
    assert gateway.config_service.upserts[-1][0]["group_name"] == "测试群"
    assert conn.info_calls == 1

    # 名字已知后不再打接口
    await gateway._remember_group(conn, "G1")
    assert conn.info_calls == 1


@pytest.mark.asyncio
async def test_remember_group_does_not_retry_denied_api():
    gateway = QqBotGateway()
    gateway.config_service = FakeConfigService()
    conn = FakeConn(fail=True)

    await gateway._remember_group(conn, "G1")
    await gateway._remember_group(conn, "G1")
    await gateway._remember_group(conn, "G1")
    assert conn.info_calls == 1, "无权限后应按群记住，不能每条消息都重试"
    assert gateway.config_service.upserts[-1][0]["group_name"] == ""
