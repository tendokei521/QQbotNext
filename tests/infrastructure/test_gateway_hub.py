"""GatewayHub 的协议路由契约。

重点回归：``bots_provider`` 必须给两个子网关各自装上**按协议过滤**的 provider。
历史 bug：Hub.__setattr__ 在 property setter 之后又无条件转发原值，把过滤后的
provider 覆盖成未过滤 → OneBot 网关把官方账号也建成连接（重启后出现同 id 幽灵）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.infrastructure.gateway_hub import GatewayHub


class FakeGateway:
    def __init__(self) -> None:
        self.connections: dict = {}
        self.bots_provider = None
        self.started_with: list = []
        # 真实网关在 __init__ 里就有这些可注入属性（Hub 靠 hasattr 决定是否转发）
        self.send_hook_registry = None
        self.before_send_hook_registry = None

    async def start_all(self, rows):
        self.started_with = list(rows)

    async def reconcile(self):
        return None


def _hub() -> tuple[GatewayHub, FakeGateway, FakeGateway]:
    onebot, qqbot = FakeGateway(), FakeGateway()
    return GatewayHub(onebot=onebot, qqbot=qqbot), onebot, qqbot


def test_bots_provider_is_filtered_per_protocol():
    hub, onebot, qqbot = _hub()
    hub.bots_provider = lambda: [
        {"id": "bot1", "protocol": "onebot"},
        {"id": "official", "protocol": "qqbot"},
    ]
    assert [r["id"] for r in onebot.bots_provider()] == ["bot1"]
    assert [r["id"] for r in qqbot.bots_provider()] == ["official"]


def test_start_all_splits_accounts_by_protocol():
    import asyncio

    hub, onebot, qqbot = _hub()
    asyncio.run(hub.start_all([
        {"id": "bot1", "protocol": "onebot"},
        {"id": "official", "protocol": "qqbot"},
    ]))
    assert [r["id"] for r in onebot.started_with] == ["bot1"]
    assert [r["id"] for r in qqbot.started_with] == ["official"]


def test_add_bot_routes_to_qqbot():
    import asyncio

    hub, onebot, qqbot = _hub()

    async def add_qqbot(*args, **kwargs):
        qqbot.connections[kwargs["account_id"]] = SimpleNamespace(protocol="qqbot")
        return kwargs["account_id"]

    qqbot.add_bot = add_qqbot  # type: ignore[assignment]
    account_id = asyncio.run(hub.add_bot(
        "", None, False, account_id="official", protocol="qqbot",
        transport="ws", app_id="a", app_secret="s",
    ))
    assert account_id == "official"
    assert "official" in hub.connections


def test_connections_is_a_live_mutable_view():
    hub, onebot, qqbot = _hub()
    conn = SimpleNamespace(protocol="onebot")
    hub.connections["bot1"] = conn
    assert onebot.connections["bot1"] is conn
    assert hub.connections["bot1"] is conn
    del hub.connections["bot1"]
    assert "bot1" not in onebot.connections


def test_qqbot_connection_is_written_to_qqbot_side():
    hub, onebot, qqbot = _hub()
    conn = SimpleNamespace(protocol="qqbot")
    hub.connections["official"] = conn
    assert "official" in qqbot.connections
    assert "official" not in onebot.connections


@pytest.mark.parametrize("name", ["send_hook_registry", "before_send_hook_registry"])
def test_hook_registries_are_forwarded(name: str):
    hub, onebot, qqbot = _hub()
    setattr(hub, name, "registry")
    assert getattr(onebot, name) == "registry"
    assert getattr(qqbot, name) == "registry"
