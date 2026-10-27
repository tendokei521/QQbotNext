"""``POST /api/bots/config/add``：先选连接方式再创建。

前端「新增账号」改成 AstrBot 那样的两步：点「添加账号」→ 选正向 / 反向 WebSocket
→ 才创建。接口因此要收 ``conn_mode``，并且**创建一个能直接用的地址**——
反向模式没有端口就起不了监听，空地址等于让用户创建完还要自己去猜。
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.webui.app import create_app


@pytest.fixture
async def client(container):
    return TestClient(create_app(container))


async def test_add_without_body_defaults_to_forward(client, config_service):
    """旧前端/脚本的无参 POST 仍是合法调用：按正向建号。"""
    resp = client.post("/api/bots/config/add")

    assert resp.status_code == 200
    body = resp.json()
    assert body["conn_mode"] == "forward"
    saved = config_service.get_bots()
    assert len(saved) == 1
    assert saved[0]["conn_mode"] == "forward"


async def test_add_reverse_creates_listenable_address(client, config_service):
    """选反向 → 存成 reverse，且地址自带端口与约定路径（能直接起监听）。"""
    resp = client.post("/api/bots/config/add", json={"conn_mode": "reverse"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["conn_mode"] == "reverse"
    assert body["ws_url"].endswith("/onebot/v11/ws")
    saved = config_service.get_bots()[0]
    assert saved["conn_mode"] == "reverse"
    assert saved["ws_url"] == body["ws_url"]


async def test_add_skips_ports_of_existing_accounts(client, config_service):
    """默认端口要避开已有账号：反向两个号撞同一端口 → 第二个必然监听失败。"""
    first = client.post("/api/bots/config/add", json={"conn_mode": "reverse"}).json()
    second = client.post("/api/bots/config/add", json={"conn_mode": "reverse"}).json()

    assert first["ws_url"] != second["ws_url"], "两个反向账号不能抢同一个端口"
    assert config_service.get_bots()[1]["ws_url"] == second["ws_url"]


async def test_add_keeps_explicit_address(client, config_service):
    """显式给地址时不得被默认值覆盖。"""
    resp = client.post("/api/bots/config/add", json={
        "conn_mode": "forward", "ws_url": "ws://10.0.0.5:4000/",
    })

    assert resp.status_code == 200
    assert config_service.get_bots()[0]["ws_url"] == "ws://10.0.0.5:4000/"


async def test_added_account_has_modules_off_by_default(client, config_service):
    """新增账号 = 模块默认关：「加个号」不能等于「整套功能悄悄上线」。

    例外只有两类：框架级 Agent（关掉就完全静默）与代码里显式声明默认启用的模块
    （`DEFAULT_ENABLED_MODULES`，如 OneBot 工具）——判定与运行时同一份常量。
    """
    from app.infrastructure.config.binding_store import (
        CORE_CAPABILITIES,
        DEFAULT_ENABLED_MODULES,
    )

    resp = client.post("/api/bots/config/add", json={"conn_mode": "forward"})
    account_id = resp.json()["id"]

    assert config_service.get_modules_default_enabled(account_id) is False

    modules = client.get("/api/modules", params={"account_id": account_id}).json()
    assert modules["agent"]["enabled"] is True, "Agent 必须保持默认开"
    exempt = set(CORE_CAPABILITIES) | set(DEFAULT_ENABLED_MODULES) | {"agent"}
    plugins = {
        name: data for name, data in modules.items()
        if name not in exempt and not data.get("hidden")
    }
    assert plugins, "容器里应加载出内置模块"
    assert all(data["enabled"] is False for data in plugins.values()), (
        "新账号的功能模块应全部默认关，直到用户显式打开"
    )
    for name in set(DEFAULT_ENABLED_MODULES) - {"agent"}:
        if name in modules:
            assert modules[name]["enabled"] is True, f"{name} 是显式默认启用的模块"
