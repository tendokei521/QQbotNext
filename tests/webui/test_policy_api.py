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


async def test_matrix_reports_account_default(container, config_service):
    """矩阵要带回"账号级默认"，前端才能显示「默认允许/默认不允许」。"""
    await _seed_groups(config_service)
    body = _client(container).get("/api/policy/matrix", params={"account_id": "acc1"}).json()

    assert body["default"]["enabled_source"] == "default"
    assert body["default"]["enabled"] is False, "Agent 未装配运行时 → 群聊默认关（安全默认）"
    assert body["default"]["scene_enabled"] is None, "没设过场景层 → null（跟随 Agent 配置）"
    assert len(body["rows"]) == 2


async def test_scene_default_sets_the_inherited_value(container, config_service):
    """「账号默认（群聊）」：写场景层后，选「继承」的群都按它解析。"""
    await _seed_groups(config_service)
    client = _client(container)

    resp = client.post("/api/policy/scene", json={
        "account_id": "acc1", "scene": "group", "enabled": True,
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["scene"] == "group"

    body = client.get("/api/policy/matrix", params={"account_id": "acc1"}).json()
    assert body["default"]["enabled"] is True
    assert body["default"]["enabled_source"] == "scene"
    assert body["default"]["scene_enabled"] is True
    assert {row["enabled_source"] for row in body["rows"]} == {"scene"}

    # 群级覆盖仍然优先：只关一个群
    client.post("/api/policy", json={
        "account_id": "acc1", "channel_id": "111", "patch": {"enabled": False},
    })
    rows = {
        row["group_id"]: row
        for row in client.get("/api/policy/matrix", params={"account_id": "acc1"}).json()["rows"]
    }
    assert rows["111"]["enabled"] is False and rows["111"]["enabled_source"] == "channel"
    assert rows["222"]["enabled"] is True and rows["222"]["enabled_source"] == "scene"

    # null = 跟随 Agent 配置（清掉场景层覆盖）
    cleared = client.post("/api/policy/scene", json={
        "account_id": "acc1", "scene": "group", "enabled": None,
    })
    assert cleared.status_code == 200, cleared.text
    body = client.get("/api/policy/matrix", params={"account_id": "acc1"}).json()
    assert body["default"]["scene_enabled"] is None
    assert body["default"]["enabled_source"] == "default"
    assert body["default"]["enabled"] is False


async def test_scene_default_rejects_bad_input(container):
    client = _client(container)
    bad_scene = client.post("/api/policy/scene", json={
        "account_id": "acc1", "scene": "topic", "enabled": True,
    })
    assert bad_scene.status_code == 400
    assert bad_scene.json()["code"] == "POLICY_BAD_SCENE"

    missing = client.post("/api/policy/scene", json={"account_id": "acc1"})
    assert missing.status_code == 400
    assert missing.json()["code"] == "POLICY_BAD_BODY"

    bad_type = client.post("/api/policy/scene", json={"account_id": "acc1", "enabled": "yes"})
    assert bad_type.status_code == 400
    assert bad_type.json()["code"] == "POLICY_BAD_BODY"


async def test_invalid_override_value_is_rejected(container):
    """白名单只解决"哪些键"，值写错必须当场拒绝（否则运行期静默失效）。"""
    client = _client(container)
    resp = client.post("/api/policy", json={
        "account_id": "acc1",
        "channel_id": "111",
        "patch": {"config": {"quiet_hours": "晚上"}},
    })
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "POLICY_BAD_VALUE"
    assert "HH:MM" in body["message"]


async def test_quiet_hours_override_is_accepted(container):
    client = _client(container)
    resp = client.post("/api/policy", json={
        "account_id": "acc1",
        "channel_id": "111",
        "patch": {"config": {"quiet_hours": "23:00-08:00"}},
    })
    assert resp.status_code == 200, resp.text
    got = client.get("/api/policy", params={"account_id": "acc1", "channel_id": "111"}).json()
    assert got["resolved"]["config"]["quiet_hours"] == "23:00-08:00"
    assert got["resolved"]["sources"]["quiet_hours"] == "channel"


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


# ==================== T0/T1：触发方式并入策略 ====================


async def test_trigger_mode_writes_back_legacy_keys(container):
    """新前端只写 trigger_mode，服务端要同步旧键（旧 UI/脚本仍可读）。"""
    client = _client(container)

    resp = client.post("/api/policy", json={
        "account_id": "acc1",
        "channel_id": "111",
        "patch": {"config": {"trigger_mode": "all", "trigger_probability": 30}},
    })

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["resolved"]["config"]["trigger_mode"] == "all"
    assert body["resolved"]["config"]["trigger_probability"] == 30
    assert body["resolved"]["sources"]["trigger_mode"] == "channel"
    row = body["override"]["config"]
    assert row["trigger_at"] is True, "旧键应双写"
    assert row["trigger_mode"] == "all"


async def test_legacy_trigger_keys_are_derived_into_mode(container):
    """旧前端只写 trigger_at/keyword：读出来要有显式 mode，不能两处漂移。"""
    client = _client(container)

    posted = client.post("/api/policy", json={
        "account_id": "acc1",
        "patch": {"config": {"trigger_at": False, "trigger_keyword": ["摸鱼"]}},
    })
    assert posted.status_code == 200, posted.text

    resolved = posted.json()["resolved"]["config"]
    assert resolved["trigger_mode"] == "keyword"
    assert resolved["trigger_probability"] == 100
    assert "trigger_mode" not in (posted.json()["override"]["config"] or {}), (
        "只写旧键时不应落一份显式 mode（否则以后改旧键会被它压住）"
    )


async def test_trigger_mode_can_be_cleared_back_to_inherit(container):
    client = _client(container)
    client.post("/api/policy", json={
        "account_id": "acc1", "channel_id": "111",
        "patch": {"config": {"trigger_mode": "none"}},
    })

    cleared = client.post("/api/policy", json={
        "account_id": "acc1", "channel_id": "111",
        "patch": {}, "clear": ["trigger_mode"],
    })

    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["resolved"]["sources"]["trigger_mode"] != "channel"


async def test_invalid_trigger_mode_is_rejected(container):
    client = _client(container)
    resp = client.post("/api/policy", json={
        "account_id": "acc1", "patch": {"config": {"trigger_mode": "sometimes"}},
    })
    assert resp.status_code == 400
    assert resp.json()["code"] == "POLICY_BAD_VALUE"


async def test_matrix_exposes_effective_trigger_mode(container, config_service):
    await _seed_groups(config_service)
    client = _client(container)
    client.post("/api/policy", json={
        "account_id": "acc1", "channel_id": "111",
        "patch": {"config": {"trigger_mode": "both", "trigger_keyword": ["AI"]}},
    })

    body = client.get("/api/policy/matrix", params={"account_id": "acc1"}).json()

    rows = {row["group_id"]: row for row in body["rows"]}
    assert rows["111"]["trigger_mode"] == "both"
    assert rows["111"]["trigger_source"] == "channel"
    assert rows["222"]["trigger_source"] != "channel", "没覆盖的群应显示继承来源"
    assert "trigger_mode" in body["default"]
