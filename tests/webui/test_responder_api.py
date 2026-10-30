"""响应权策略 API：声明校验 + 策略持久化 + 兼容语义。"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.webui.api.deps import get_module_registry
from app.webui.app import create_app


class _FakeModule:
    def __init__(self, name: str, declared: bool, display: str = "", sign: str = "") -> None:
        self.module_name = name
        self.name = display or name
        self.sign = sign or name
        self.description = f"{name} 描述"
        self.supports_single_service = declared


class _FakeRegistry:
    def __init__(self, modules: dict[str, _FakeModule]) -> None:
        self._modules = modules

    def get(self, module_name, bot_id=None):
        return self._modules.get(module_name)

    def loaded(self):
        # 声明是类属性：任一账号实例等价，这里给每个能力一个实例即可
        return list(self._modules.values())


@pytest.fixture
def client(container):
    app = create_app(container)
    app.dependency_overrides[get_module_registry] = lambda: _FakeRegistry({
        "declared_mod": _FakeModule("declared_mod", True, "声明插件", "Decl"),
        "plain_mod": _FakeModule("plain_mod", False, "普通插件", "Plain"),
    })
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_module_registry, None)


def test_undeclared_module_reports_not_declared(client):
    resp = client.get("/api/modules/plain_mod/responder")

    assert resp.status_code == 200
    assert resp.json() == {"module": "plain_mod", "declared": False, "groups": []}


def test_declared_module_lists_groups(client):
    resp = client.get("/api/modules/declared_mod/responder")

    assert resp.status_code == 200
    body = resp.json()
    assert body["declared"] is True
    assert body["groups"] == []


def test_missing_module_returns_404(client):
    resp = client.get("/api/modules/ghost/responder")

    assert resp.status_code == 404


def test_post_rejects_undeclared_module(client):
    resp = client.post("/api/modules/plain_mod/responder", json={"policies": []})

    assert resp.status_code == 403


def test_post_persists_channel_policies(client, config_service):
    resp = client.post("/api/modules/declared_mod/responder", json={
        "policies": [
            {"group_id": "10", "policy": "dedupe", "fallback": "closed"},
            {"group_id": "11", "policy": "leader", "primary_account_id": "acc1", "fallback": "open"},
        ],
    })

    assert resp.status_code == 200
    body = resp.json()
    # 容器里没有配置账号 → 指定账号会被视为未知（安全降级为 fallback）
    assert body["warnings"]
    assert [row["channel_id"] for row in body["policies"]] == ["10", "11"]
    saved = {row["channel_id"]: row for row in config_service.list_channel_policies("declared_mod")}
    assert saved["10"]["policy"] == "dedupe"
    assert saved["10"]["fallback"] == "closed"
    assert saved["11"]["policy"] == "leader"
    assert saved["11"]["primary_account_id"] is None


def test_post_clears_policies_with_empty_list(client, config_service):
    client.post("/api/modules/declared_mod/responder", json={
        "policies": [{"group_id": "10", "policy": "all"}],
    })

    client.post("/api/modules/declared_mod/responder", json={"policies": []})

    assert config_service.list_channel_policies("declared_mod") == []


def test_post_warns_on_unknown_primary(client, config_service):
    resp = client.post("/api/modules/declared_mod/responder", json={
        "policies": [
            {"group_id": "10", "policy": "delegate", "primary_account_id": "ghost"},
        ],
    })

    assert resp.status_code == 200
    assert resp.json()["warnings"]
    saved = config_service.list_channel_policies("declared_mod")[0]
    assert saved["primary_account_id"] is None


def test_agent_virtual_module_is_always_declared(client):
    resp = client.get("/api/modules/agent/responder")

    assert resp.status_code == 200
    assert resp.json()["declared"] is True


# ==================== 独立面板：总览接口 ====================


def test_overview_separates_declared_and_undeclared(client):
    """面板只列插件；框架级 agent 不出现（它不归这个面板管）。"""
    body = client.get("/api/responder/overview").json()

    assert body["status"] == "success"
    declared = {item["capability_id"] for item in body["declared"]}
    undeclared = {item["capability_id"] for item in body["undeclared"]}
    assert declared == {"declared_mod"}
    assert undeclared == {"plain_mod"}
    assert "agent" not in declared and "agent" not in undeclared
    item = body["declared"][0]
    assert item["name"] == "声明插件"


async def test_overview_summary_counts_only_multi_account_groups(client, config_service):
    """摘要只看「同群 ≥2 账号」的群：单账号群配 delegate 没有意义。"""
    await config_service.add_bot({"id": "acc1", "name": "主号", "ws_url": "", "auto_connect": False})
    await config_service.add_bot({"id": "acc2", "name": "副号", "ws_url": "", "auto_connect": False})
    await config_service.upsert_known_groups("acc1", [
        {"group_id": "111", "group_name": "摸鱼群", "member_count": 10},
        {"group_id": "222", "group_name": "工作群", "member_count": 5},
    ])
    await config_service.upsert_known_groups("acc2", [
        {"group_id": "111", "group_name": "摸鱼群", "member_count": 10},
    ])
    await config_service.save_channel_policies("declared_mod", [
        {"channel_id": "111", "policy": "delegate", "primary_account_id": "acc2", "fallback": "open"},
        {"channel_id": "222", "policy": "dedupe", "fallback": "open"},  # 只有 acc1 → 不计入
    ])

    body = client.get("/api/responder/overview").json()
    item = body["declared"][0]

    assert item["groups_total"] == 1, "222 只有 1 个账号，不该算进可配置群"
    assert item["configured"] == 1
    assert item["policies"] == {"delegate": 1}


def test_module_payload_carries_display_meta(client):
    """独立配置页需要标题/签名，不必再去 /api/modules 拉一次（且那里是账号视角）。"""
    body = client.get("/api/modules/declared_mod/responder").json()

    assert body["name"] == "声明插件"
    assert body["sign"] == "Decl"
    assert body["description"]


async def test_offline_cached_groups_are_configurable(client, config_service):
    """账号离线时也要能配：群列表回退到登录时落库的 known_groups。"""
    await config_service.add_bot({"id": "acc1", "name": "主号", "ws_url": "", "auto_connect": False})
    await config_service.upsert_known_groups("acc1", [
        {"group_id": "111", "group_name": "摸鱼群", "member_count": 10},
    ])

    body = client.get("/api/modules/declared_mod/responder").json()

    assert body["declared"] is True
    assert [group["group_id"] for group in body["groups"]] == ["111"]
    assert body["groups"][0]["name"] == "摸鱼群"
