"""P1-1：就绪诊断聚合（总览就绪卡 / 启动横幅 / 向导完成判定共用）。

判定口径（docs/oneclick-bundle-and-onboarding-design.md §6.5）：
blocker 决定 ``ready``；warn 只提示。这样"没账号/没模型/场景全关"会直接
告诉用户差什么，而不是让他在群里 @ 一下得到沉默。
"""

from __future__ import annotations

from types import SimpleNamespace

from starlette.testclient import TestClient

from app.agent.runtime import AgentManager
from app.application.bot_service import BotService
from app.webui.app import create_app


def _client(container) -> TestClient:
    return TestClient(create_app(container))


def _fake_runtime(config: dict):
    return SimpleNamespace(config=SimpleNamespace(raw_config=dict(config)))


def test_fresh_install_is_not_ready(container):
    body = _client(container).get("/api/readiness").json()

    assert body["ok"] is True
    assert body["ready"] is False
    assert body["setup_completed"] is False
    blockers = {c["id"] for c in body["checks"] if not c["ok"] and c["level"] == "blocker"}
    assert {"account", "connected", "model_pool", "scene_enabled"} <= blockers
    for check in body["checks"]:
        if check["id"] in blockers:
            assert check["action"] and check["action"]["route"], f"{check['id']} 必须给出修复入口"


async def test_ready_when_account_connected_model_and_scene_ok(container, config_service):
    await config_service.save_bots([
        {"id": "acc1", "name": "主号", "ws_url": "ws://127.0.0.1:3001", "auto_connect": True},
    ])
    gateway = container.get(BotService).gateway
    gateway.connections["acc1"] = SimpleNamespace(status="connected", conn_mode="forward")

    manager = container.get(AgentManager)
    manager.runtimes = lambda: {  # type: ignore[assignment]
        "acc1": _fake_runtime({
            "group_enable": True,
            "private_enable": True,
            "provider_model_pool": ["model-1"],
        })
    }

    body = _client(container).get("/api/readiness").json()

    assert body["ready"] is True, body
    by_id = {check["id"]: check for check in body["checks"]}
    assert by_id["account"]["ok"] and by_id["connected"]["ok"]
    assert by_id["model_pool"]["ok"] and by_id["scene_enabled"]["ok"]


async def test_missing_model_is_a_blocker(container, config_service):
    await config_service.save_bots([
        {"id": "acc1", "name": "主号", "ws_url": "ws://127.0.0.1:3001", "auto_connect": True},
    ])
    container.get(BotService).gateway.connections["acc1"] = SimpleNamespace(
        status="connected", conn_mode="forward"
    )
    manager = container.get(AgentManager)
    manager.runtimes = lambda: {  # type: ignore[assignment]
        "acc1": _fake_runtime({"group_enable": True, "private_enable": True}),
    }

    body = _client(container).get("/api/readiness").json()

    assert body["ready"] is False
    by_id = {check["id"]: check for check in body["checks"]}
    assert by_id["model_pool"]["ok"] is False
    assert by_id["scene_enabled"]["ok"] is True


async def test_reverse_not_listening_is_only_a_warning(container, config_service):
    await config_service.save_bots([
        {"id": "rev", "name": "反向号", "ws_url": "ws://127.0.0.1:3001/onebot/v11/ws",
         "conn_mode": "reverse"},
    ])
    container.get(BotService).gateway.connections["rev"] = SimpleNamespace(
        status="connected", conn_mode="reverse"
    )
    manager = container.get(AgentManager)
    manager.runtimes = lambda: {  # type: ignore[assignment]
        "rev": _fake_runtime({"group_enable": True, "provider_model_pool": ["m"]}),
    }

    body = _client(container).get("/api/readiness").json()
    by_id = {check["id"]: check for check in body["checks"]}

    assert by_id["protocol_listen"]["level"] == "warn"
    assert by_id["protocol_listen"]["ok"] is False
    assert body["ready"] is True, "warn 不该让整体不可用"


async def test_setup_complete_is_persisted(container, config_service):
    client = _client(container)
    resp = client.post("/api/setup/complete")
    assert resp.status_code == 200, resp.text

    assert await config_service.get_kv("setup_completed_at", 0)
    body = client.get("/api/readiness").json()
    assert body["setup_completed"] is True


async def test_setup_complete_stops_background_retry(container, config_service):
    """"完成"之后不该再有后台重试在跑（用户之后可在账号管理里手动连）。"""
    from starlette.testclient import TestClient

    from app.application.setup_account import SetupAccountService
    from app.webui.app import create_app

    service = container.get(SetupAccountService)
    await config_service.save_bots([
        {"id": "acc1", "name": "主号", "ws_url": "ws://127.0.0.1:4001/"},
    ])
    service.connect_loop.ensure("acc1")

    resp = TestClient(create_app(container)).post("/api/setup/complete")

    assert resp.status_code == 200, resp.text
    assert service.connect_loop.running("acc1") is False


async def test_readiness_warns_when_account_has_no_address(container, config_service):
    """正向「向外连接」可以先不填地址，但就绪清单要指出这一点（否则"去连接"必然失败）。"""
    from starlette.testclient import TestClient

    from app.webui.app import create_app

    await config_service.save_bots([
        {"id": "acc1", "name": "主号", "ws_url": "", "conn_mode": "forward"},
    ])
    body = TestClient(create_app(container)).get("/api/readiness").json()

    by_id = {check["id"]: check for check in body["checks"]}
    assert "account_address" in by_id
    assert by_id["account_address"]["ok"] is False
    assert by_id["account_address"]["level"] == "warn"
    assert "主号" in by_id["account_address"]["detail"], "文案应给账号名而不是 id"
    assert by_id["account_address"]["action"]["account_id"] == "acc1"


async def test_readiness_has_no_address_warning_when_filled(container, config_service):
    from starlette.testclient import TestClient

    from app.webui.app import create_app

    await config_service.save_bots([
        {"id": "acc1", "name": "主号", "ws_url": "ws://127.0.0.1:4001/", "conn_mode": "reverse"},
    ])
    body = TestClient(create_app(container)).get("/api/readiness").json()

    assert "account_address" not in {check["id"] for check in body["checks"]}


async def test_model_pool_reports_which_accounts_are_missing(container, config_service):
    """多账号时要说清"哪个号没选模型"，并把跳转目标指向那个号。"""
    from starlette.testclient import TestClient

    from app.agent.runtime import AgentManager
    from app.webui.app import create_app

    await config_service.save_bots([
        {"id": "good", "name": "配好的号", "ws_url": "ws://127.0.0.1:4001/"},
        {"id": "bad", "name": "没配的号", "ws_url": "ws://127.0.0.1:4002/"},
    ])
    manager = container.get(AgentManager)
    manager.ensure_runtime("good").config.set("provider_model_pool", ["pm1"], auto_save=False)
    manager.ensure_runtime("bad")

    body = TestClient(create_app(container)).get("/api/readiness").json()
    check = next(c for c in body["checks"] if c["id"] == "model_pool")

    assert check["ok"] is False
    assert "没配的号" in check["detail"] and "配好的号" not in check["detail"]
    assert check["action"]["account_id"] == "bad"
    assert check["action"]["route"] == "/agent/basic"
