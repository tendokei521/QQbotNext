"""R0-1：群列表缓存 + 刷新接口。

背景（docs/permission-dialogue-policy-design.md §1.2）：``get_group_list`` 原先只在
连接成功时拉一次、结果只在内存里，账号一断开 WebUI 就看不到任何群，也就无法
"拉取群再勾哪个群能聊天"。本测试锁定：

1. refresh 会把群列表写进 ``known_groups``（离线可读）；
2. GET 不产生副作用，缓存优先，并给出 ``stale`` / ``source``；
3. 未连接/未知账号 → 409 + 可读原因（不是 500）；
4. 第二次刷新时"已退出的群"标记 ``in_group=0`` 但不删除。
"""

from __future__ import annotations

from starlette.testclient import TestClient

from app.application.bot_service import BotService
from app.webui.app import create_app


class _FakeConn:
    """最小连接替身：只提供群列表相关字段与方法。"""

    def __init__(self, groups: list[dict], *, status: str = "connected") -> None:
        self.status = status
        self.all_group_list_info = [
            {"group_id": g["group_id"], "group_name": g.get("group_name", ""),
             "member_count": g.get("member_count", 0)}
            for g in groups
        ]
        self.all_group_list = [g["group_id"] for g in groups]
        self._groups = groups
        self.calls = 0

    async def get_group_list(self) -> dict:
        self.calls += 1
        return {"status": "ok", "data": self._groups}


class _FailingConn(_FakeConn):
    async def get_group_list(self) -> dict:
        return {"status": "failed", "message": "NapCat 未实现该接口"}


def _client(container) -> TestClient:
    return TestClient(create_app(container))


def _gateway(container):
    return container.get(BotService).gateway


def test_refresh_persists_groups(container, config_service):
    groups = [
        {"group_id": 111, "group_name": "前端交流群", "member_count": 24},
        {"group_id": 222, "group_name": "测试群", "member_count": 8},
    ]
    conn = _FakeConn(groups)
    _gateway(container).connections["acc1"] = conn

    resp = _client(container).post("/api/groups/refresh", params={"account_id": "acc1"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 2
    cached = config_service.list_known_groups("acc1")
    assert {row["group_id"] for row in cached} == {"111", "222"}
    assert all(row["in_group"] for row in cached)
    assert conn.calls == 1
    assert conn.all_group_list == [111, 222]


def test_get_prefers_cache_and_reports_staleness(container, config_service):
    conn = _FakeConn([{"group_id": 111, "group_name": "前端交流群"}])
    _gateway(container).connections["acc1"] = conn
    client = _client(container)
    client.post("/api/groups/refresh", params={"account_id": "acc1"})

    # 刚刷新过 → 缓存新鲜
    fresh = client.get("/api/groups", params={"account_id": "acc1"}).json()
    assert fresh["source"] == "cache"
    assert fresh["stale"] is False

    # 账号断开：缓存仍在，仍可读；把时间戳推老 → stale=true
    conn.status = "disconnected"
    for row in config_service._known_groups.values():
        row["updated_at"] = 0
    resp = client.get("/api/groups", params={"account_id": "acc1"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "cache"
    assert body["groups"][0]["group_id"] == "111"
    assert body["stale"] is True
    assert body["connected"] is False


def test_get_without_any_cache_falls_back_to_live_memory(container):
    conn = _FakeConn([{"group_id": 333, "group_name": "摸鱼群"}])
    _gateway(container).connections["acc9"] = conn
    client = _client(container)

    resp = client.get("/api/groups", params={"account_id": "acc9"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "live"
    assert body["groups"][0]["group_id"] == "333"


def test_refresh_rejects_unknown_account(container):
    resp = _client(container).post("/api/groups/refresh", params={"account_id": "nobody"})
    assert resp.status_code == 409
    assert "不存在" in resp.json()["message"]


def test_refresh_rejects_disconnected_account(container):
    conn = _FakeConn([], status="disconnected")
    _gateway(container).connections["acc1"] = conn
    resp = _client(container).post("/api/groups/refresh", params={"account_id": "acc1"})
    assert resp.status_code == 409
    assert "未连接" in resp.json()["message"]


def test_refresh_surfaces_protocol_failure(container):
    _gateway(container).connections["acc1"] = _FailingConn([])
    resp = _client(container).post("/api/groups/refresh", params={"account_id": "acc1"})
    assert resp.status_code == 409
    assert "未返回群列表" in resp.json()["message"]


def test_second_refresh_marks_left_groups_without_deleting(container, config_service):
    conn = _FakeConn([
        {"group_id": 111, "group_name": "留着的群"},
        {"group_id": 222, "group_name": "要退的群"},
    ])
    _gateway(container).connections["acc1"] = conn
    client = _client(container)
    client.post("/api/groups/refresh", params={"account_id": "acc1"})

    conn._groups = [{"group_id": 111, "group_name": "留着的群"}]
    client.post("/api/groups/refresh", params={"account_id": "acc1"})

    rows = {row["group_id"]: row for row in config_service.list_known_groups("acc1")}
    assert rows["111"]["in_group"] is True
    assert rows["222"]["in_group"] is False, "退群要留痕（历史设置不能因为退群丢失）"
    assert len(rows) == 2
