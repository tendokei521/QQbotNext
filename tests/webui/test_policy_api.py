"""R1：对话策略 API（读/写/批量/静音/矩阵）。

对应设计 docs/permission-dialogue-policy-design.md §4 的端点契约：
简单视图只写 ``enabled``，高级视图写白名单键；两者共享同一份 ``channel_overrides``。
"""

from __future__ import annotations

from starlette.testclient import TestClient

from app.webui.app import create_app


def _client(container) -> TestClient:
    return TestClient(create_app(container))


def _seed_groups(config_service, account_id="acc1"):
    return config_service.upsert_known_groups(account_id, [
        {"group_id": 111, "group_name": "前端交流群", "member_count": 24},
        {"group_id": 222, "group_name": "测试群", "member_count": 8},
    ])


async def test_get_policy_returns_defaults_and_overridable_keys(container, config_service):
    client = _client(container)
    resp = client.get("/api/policy", params={"account_id": "acc1", "channel_id": "111"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["resolved"]["capability"] == "agent"
    assert body["resolved"]["enabled_source"] == "default"
    assert "trigger_at" in body["overridable"]
    assert body["permission_keys"] == ["role_min", "user_mode", "user_list"]
    assert body["overrides"] == {"account": None, "channel": None}


async def test_put_group_override_and_read_back(container):
    client = _client(container)
    resp = client.post("/api/policy", json={
        "account_id": "acc1",
        "channel_id": "111",
        "patch": {"enabled": True},
    })
    assert resp.status_code == 200, resp.text

    got = client.get("/api/policy", params={"account_id": "acc1", "channel_id": "111"}).json()
    assert got["resolved"]["enabled"] is True
    assert got["resolved"]["enabled_source"] == "channel"
    assert got["overrides"]["channel"]["enabled"] is True


async def test_unoverridable_key_is_rejected(container):
    client = _client(container)
    resp = client.post("/api/policy", json={
        "account_id": "acc1",
        "channel_id": "111",
        "patch": {"config": {"max_tokens": 4096}},
    })
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "POLICY_KEY_NOT_OVERRIDABLE"
    assert "max_tokens" in body["message"]


async def test_clear_keys_restores_inheritance(container):
    client = _client(container)
    client.post("/api/policy", json={
        "account_id": "acc1",
        "channel_id": "111",
        "patch": {"enabled": True, "config": {"trigger_at": True}},
    })

    resp = client.post("/api/policy", json={
        "account_id": "acc1",
        "channel_id": "111",
        "patch": {"enabled": None},
        "clear": ["trigger_at"],
    })
    assert resp.status_code == 200, resp.text

    got = client.get("/api/policy", params={"account_id": "acc1", "channel_id": "111"}).json()
    assert got["resolved"]["enabled_source"] == "default", "恢复继承后不该再有覆盖"
    assert got["overrides"]["channel"] is None


async def test_bulk_toggle_and_matrix(container, config_service):
    await _seed_groups(config_service)
    client = _client(container)

    resp = client.post("/api/policy/bulk", json={
        "account_id": "acc1",
        "channel_ids": ["111", "222"],
        "enabled": True,
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["changed"] == 2

    matrix = client.get("/api/policy/matrix", params={"account_id": "acc1"}).json()["rows"]
    by_group = {row["group_id"]: row for row in matrix}
    assert by_group["111"]["enabled"] is True
    assert by_group["222"]["enabled"] is True
    assert by_group["111"]["enabled_source"] == "channel"

    # null = 恢复继承
    client.post("/api/policy/bulk", json={
        "account_id": "acc1",
        "channel_ids": ["111", "222"],
        "enabled": None,
    })
    matrix = client.get("/api/policy/matrix", params={"account_id": "acc1"}).json()["rows"]
    assert {row["enabled_source"] for row in matrix} == {"default"}


async def test_bulk_rejects_non_bool_enabled(container, config_service):
    await _seed_groups(config_service)
    resp = _client(container).post("/api/policy/bulk", json={
        "account_id": "acc1",
        "channel_ids": ["111"],
        "enabled": "yes",
    })
    assert resp.status_code == 400
    assert resp.json()["code"] == "POLICY_BAD_BODY"


async def test_mute_overrides_enabled_until_cancelled(container):
    client = _client(container)
    client.post("/api/policy", json={"account_id": "acc1", "channel_id": "111", "patch": {"enabled": True}})

    muted = client.post("/api/policy/mute", json={
        "account_id": "acc1", "channel_id": "111", "minutes": 60,
    })
    assert muted.status_code == 200, muted.text
    got = client.get("/api/policy", params={"account_id": "acc1", "channel_id": "111"}).json()
    assert got["resolved"]["enabled"] is False
    assert got["resolved"]["enabled_source"] == "muted"

    client.post("/api/policy/mute", json={"account_id": "acc1", "channel_id": "111", "minutes": 0})
    got = client.get("/api/policy", params={"account_id": "acc1", "channel_id": "111"}).json()
    assert got["resolved"]["enabled"] is True, "取消静音后应回到原覆盖值"
    assert got["resolved"]["enabled_source"] == "channel"


async def test_bad_json_and_missing_account_are_rejected(container):
    client = _client(container)
    bad = client.post(
        "/api/policy",
        content=b"{oops",
        headers={"Content-Type": "application/json"},
    )
    assert bad.status_code == 400
    assert bad.json()["code"] == "POLICY_BAD_JSON"

    missing = client.get("/api/policy")
    assert missing.status_code == 400
    assert missing.json()["code"] == "POLICY_ACCOUNT_REQUIRED"
