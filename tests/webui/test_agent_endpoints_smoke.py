"""Agent 端点冒烟：把 `container` 改成具名依赖后，逐个真打一遍。

改 `Depends` 时最容易出的错是「签名改了但没人调到」——`logs.py` 那次就是这样漏了
三个端点，全量测试却仍是绿的。这里做两件事：

1. 从 router 里**动态取出**真实路径（写死路径会让「路由不存在」也悄悄通过）；
2. 每个 GET 端点都必须返回 200 或**业务 404**（`{"status": "error"}`），
   而不是 FastAPI 的 404 `{"detail": "Not Found"}`——后者说明路由根本没挂上。
"""

from __future__ import annotations

import pytest


@pytest.fixture
def client(container):
    from starlette.testclient import TestClient

    from app.webui.app import create_app

    return TestClient(create_app(container))


def test_agent_get_endpoints_resolve_dependencies(client, container):
    """每个 GET 端点都要真的被路由到，且依赖装配成功（不是 500，也不是「路由不存在」）。"""
    from app.webui.api import agent as agent_router

    paths = sorted({r.path for r in agent_router.router.routes if "GET" in getattr(r, "methods", set())})
    assert paths, "agent router 没有 GET 路由？"

    bad: list[tuple[str, int, str]] = []
    for path in paths:
        r = client.get("/api" + path)
        if r.status_code == 200:
            continue
        # 业务 404（例如「该 bot 无 Agent 运行时」）是合法的
        if r.status_code == 404 and "status" in r.text and "detail" not in r.text:
            continue
        bad.append((path, r.status_code, r.text[:140]))
    assert not bad, f"这些端点没有正常响应: {bad}"
