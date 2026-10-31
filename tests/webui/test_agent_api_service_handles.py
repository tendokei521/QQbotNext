"""回归测试：模块未安装时，WebUI 的相关接口必须**降级返回**而不是 500。

历史 bug：`/api/agent/mcp/tools` 直读 `runtime.mcp_manager`，而 MCP 已搬进
`llm_tools_extra` 模块 → AttributeError → 500，前端弹「Failed to load resource」。

同类接口：知识库 / 定时任务 / 主动消息 / 模块工具。
它们的实现都归模块，框架只持「服务句柄」（`knowledge_service` / `mcp_service` /
`proactive_service` / `scheduler`）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.webui.api import agent as agent_api


class _AgentManager:
    """最小 agent manager：提供 get_runtime。"""

    def __init__(self, runtime):
        self._runtime = runtime

    def get_runtime(self, bot_id):
        return self._runtime


@pytest.fixture
def client():
    """运行时**什么服务都没挂**（模拟模块未安装）。"""
    runtime = SimpleNamespace(
        bot_id="3437542570",
        config={},
        llm_tools=None,
        llm_tools_registry=None,
    )
    app = FastAPI()
    app.include_router(agent_api.router, prefix="/api")  # 路由自带 /agent 前缀
    app.state.container = SimpleNamespace(get=lambda cls: None)
    app.dependency_overrides[agent_api.get_agent_manager] = lambda: _AgentManager(runtime)
    return TestClient(app)


def test_mcp_tools_degrades_without_module(client):
    resp = client.get("/api/agent/mcp/tools", params={"bot_id": 3437542570})
    assert resp.status_code == 200, f"MCP 未安装时应降级，实际 {resp.status_code}"
    body = resp.json()
    assert body["available"] is False
    assert body["tools"] == []


def test_knowledge_items_degrades_without_module(client):
    resp = client.get("/api/agent/knowledge/items", params={"bot_id": 3437542570})
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["items"] == []


def test_tasks_degrades_without_module(client):
    resp = client.get("/api/agent/tasks", params={"bot_id": 3437542570})
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False


def test_proactive_status_degrades_without_module(client):
    resp = client.get("/api/agent/proactive/status", params={"bot_id": 3437542570})
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["sessions"] == []


def test_module_tools_tolerates_missing_registry(client):
    resp = client.get("/api/agent/module/tools", params={"bot_id": 3437542570})
    assert resp.status_code == 200
    assert resp.json()["tools"] == []


def test_proactive_trigger_reports_not_enabled(client):
    resp = client.post(
        "/api/agent/proactive/trigger",
        params={"bot_id": 3437542570},
        json={"session_id": "group_1"},
    )
    assert resp.status_code == 400
    assert "未启用" in resp.json()["message"]
