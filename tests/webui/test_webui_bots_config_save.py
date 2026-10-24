"""``POST /api/bots/config/save`` 不能把"解析失败"变成"清空所有账号"。

审查发现的破坏性缺陷（`webui/api/bots.py`）：

    try:
        data = await request.json()
    except Exception:
        data = {}
    await container.get(ConfigService).save_bots(data.get("bots", []))
    return _ok("配置已保存")

而 ``save_bots`` 会删掉 payload 里没有的账号，于是：body 被代理截断 / 非法 JSON /
前端字段名漂移 → 全部账号配置被清空、接口回「配置已保存」、重启后无号可连。

同仓库其他写接口缺字段一律 400（sessions / agent），只有这里曾把"缺省值"
写进了破坏性事务。v4 起还多了一条：**账号 id 非法/重复 → 400，且不改动任何账号**。
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
        {"id": "a", "name": "甲", "ws_url": "ws://127.0.0.1:3001", "owner_id": 10001,
         "auto_connect": True},
        {"id": "b", "name": "乙", "ws_url": "ws://127.0.0.1:3002", "owner_id": 10002,
         "auto_connect": False},
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


async def test_invalid_account_id_keeps_accounts(client, config_service):
    """id 非法 → 400，且一个账号都不能动（不能"改一半"）。"""
    before = await _seed(config_service)

    resp = client.post("/api/bots/config/save", json={
        "bots": [{"id": "../evil", "ws_url": "ws://127.0.0.1:4001"}],
    })

    assert resp.status_code == 400
    assert [b["ws_url"] for b in config_service.get_bots_public()] == before


async def test_duplicate_account_id_keeps_accounts(client, config_service):
    before = await _seed(config_service)

    resp = client.post("/api/bots/config/save", json={
        "bots": [
            {"id": "dup", "ws_url": "ws://127.0.0.1:4001"},
            {"id": "dup", "ws_url": "ws://127.0.0.1:4002"},
        ],
    })

    assert resp.status_code == 400
    assert [b["ws_url"] for b in config_service.get_bots_public()] == before


async def test_valid_payload_still_saves(client, config_service):
    await _seed(config_service)

    resp = client.post("/api/bots/config/save", json={
        "bots": [{"id": "a", "name": "甲", "ws_url": "ws://127.0.0.1:4001",
                  "owner_id": 20002, "auto_connect": True}],
    })

    assert resp.status_code == 200
    public = config_service.get_bots_public()
    assert [b["ws_url"] for b in public] == ["ws://127.0.0.1:4001"]
    # id 稳定的好处：删掉 b 不会影响 a 的身份
    assert [b["id"] for b in public] == ["a"]


async def test_rename_through_api_keeps_id(client, config_service):
    await _seed(config_service)

    resp = client.post("/api/bots/config/save", json={
        "bots": [
            {"id": "a", "name": "甲改名", "ws_url": "ws://127.0.0.1:3001"},
            {"id": "b", "name": "乙", "ws_url": "ws://127.0.0.1:3002"},
        ],
    })

    assert resp.status_code == 200
    public = config_service.get_bots_public()
    assert [(b["id"], b["name"]) for b in public] == [("a", "甲改名"), ("b", "乙")]


# ==================== 连接方向（正向 / 反向 WS）====================


async def test_conn_mode_saved_through_api(client, config_service):
    """配置页保存的连接方式必须一路落到 get_bots_public（前端重新加载要读到它）。"""
    resp = client.post("/api/bots/config/save", json={"bots": [
        {"id": "rev", "name": "反向号", "ws_url": "ws://127.0.0.1:3001/onebot/v11/ws",
         "conn_mode": "reverse", "access_token": "tk", "auto_connect": True},
        {"id": "fwd", "name": "正向号", "ws_url": "ws://127.0.0.1:3002/",
         "conn_mode": "forward", "auto_connect": False},
    ]})

    assert resp.status_code == 200
    public = config_service.get_bots_public()
    assert [b["conn_mode"] for b in public] == ["reverse", "forward"]
    assert public[0]["access_token"] == "tk"


async def test_legacy_client_without_conn_mode_keeps_direction(client, config_service):
    """老前端/旧脚本全量保存时不带 conn_mode：已存的反向账号不能被改回正向。"""
    client.post("/api/bots/config/save", json={"bots": [
        {"id": "rev", "name": "反向号", "ws_url": "ws://127.0.0.1:3001/onebot/v11/ws",
         "conn_mode": "reverse"},
    ]})
    # 只改 auto_connect，payload 里没有 conn_mode
    client.post("/api/bots/config/save", json={"bots": [
        {"id": "rev", "name": "反向号", "ws_url": "ws://127.0.0.1:3001/onebot/v11/ws",
         "auto_connect": True},
    ]})
    assert config_service.get_bots_public()[0]["conn_mode"] == "reverse"
