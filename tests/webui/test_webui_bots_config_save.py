"""``POST /api/bots/config/save`` 不能把"解析失败"变成"清空所有账号"。

审查发现的破坏性缺陷（`webui/api/bots.py`）：

    try:
        data = await request.json()
    except Exception:
        data = {}
    await container.get(ConfigService).save_bots(data.get("bots", []))
    return _ok("配置已保存")

而 ``save_bots`` 的第一条 SQL 就是 ``DELETE FROM bots``，随后按 payload 重建，
末尾还会 ``drop_bot_accounts_from(len(normalized))`` 清掉上次登录快照。
于是：body 被代理截断 / 非法 JSON / 前端字段名漂移 → 全部账号配置被清空、
接口回「配置已保存」、重启后无号可连。

同仓库其他写接口缺字段一律 400（sessions / agent），
只有这里把"缺省值"写进了破坏性事务。
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.webui.app import create_app


@pytest.fixture
async def client(container):
    return TestClient(create_app(container))


async def _seed(config_service):
    await config_service.save_bots([
        {"ws_url": "ws://127.0.0.1:3001", "owner_id": 10001, "auto_connect": True},
        {"ws_url": "ws://127.0.0.1:3002", "owner_id": 10002, "auto_connect": False},
    ])
    return [b["ws_url"] for b in config_service.get_bots_public()]


async def test_invalid_json_keeps_accounts(client, config_service):
    before = await _seed(config_service)

    resp = client.post(
        "/api/bots/config/save",
        content=b"{not json at all",
        headers={"Content-Type": "application/json"},
    )

    assert resp.status_code == 400
    assert [b["ws_url"] for b in config_service.get_bots_public()] == before


async def test_missing_bots_key_keeps_accounts(client, config_service):
    before = await _seed(config_service)

    resp = client.post("/api/bots/config/save", json={"accounts": []})

    assert resp.status_code == 400
    assert [b["ws_url"] for b in config_service.get_bots_public()] == before


async def test_wrong_bots_type_keeps_accounts(client, config_service):
    before = await _seed(config_service)

    resp = client.post("/api/bots/config/save", json={"bots": "not-a-list"})

    assert resp.status_code == 400
    assert [b["ws_url"] for b in config_service.get_bots_public()] == before


async def test_non_object_body_keeps_accounts(client, config_service):
    before = await _seed(config_service)

    resp = client.post("/api/bots/config/save", json=[1, 2, 3])

    assert resp.status_code == 400
    assert [b["ws_url"] for b in config_service.get_bots_public()] == before


async def test_explicit_empty_list_still_deletes_all(client, config_service):
    """空数组是**用户主动删光**的合法请求，不能被 400 误伤。"""
    await _seed(config_service)

    resp = client.post("/api/bots/config/save", json={"bots": []})

    assert resp.status_code == 200
    assert config_service.get_bots_public() == []


async def test_valid_payload_still_saves(client, config_service):
    await _seed(config_service)

    resp = client.post("/api/bots/config/save", json={
        "bots": [{"ws_url": "ws://127.0.0.1:4001", "owner_id": 20002, "auto_connect": True}],
    })

    assert resp.status_code == 200
    assert [b["ws_url"] for b in config_service.get_bots_public()] == ["ws://127.0.0.1:4001"]
