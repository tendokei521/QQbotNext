"""模块热重载 API：必须按 **account_id** 查连接，不能按 QQ 号。

历史 bug：`find_conn_by_bot_id(bot_id)` 里的 ``bot_id`` 实际是 account_id 字符串，
与 ``conn.bot_id``（QQ 整数）永不相等 → 拿到 None；reload 后实例 ``ctx.bot=None``、
``online=False``，定时任务/主动消息不注册。

修法：统一走 ``gateway.get_bot(account_id)``（按 ``connections`` 的 account_id 查）。
"""

from __future__ import annotations

from app.webui.api import modules


class _Conn:
    pass


class _Gateway:
    def __init__(self) -> None:
        self.conn = _Conn()
        self.asked: list[str] = []

    def get_bot(self, account_id: str):
        self.asked.append(account_id)
        return self.conn

    def find_conn_by_bot_id(self, _bot_id):
        raise AssertionError("热重载必须按 account_id 查连接，不能退回按 QQ 号查")


class _ReloadRegistry:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.seen: tuple | None = None

    async def reload_all(self, bot_id, bot=None):
        self.seen = (bot_id, bot)
        return 1

    async def reload_single(self, module_name, bot_id, bot=None):
        self.seen = (module_name, bot_id, bot)
        return self.ok


class _BotService:
    def __init__(self, registry) -> None:
        self.gateway = _Gateway()
        self.registry = registry


class _Req:
    pass


async def test_reload_all_uses_account_id_lookup(monkeypatch):
    sent: list[str] = []

    async def fake_broadcast(message: str) -> None:
        sent.append(message)

    monkeypatch.setattr(modules.manager, "broadcast", fake_broadcast)
    registry = _ReloadRegistry()
    service = _BotService(registry)

    resp = await modules.reload_modules(_Req(), bot_id="acc1", bot_service=service)

    assert resp.status_code == 200
    assert service.gateway.asked == ["acc1"], "应当按账号 id 查连接"
    assert registry.seen == ("acc1", service.gateway.conn), "连接必须传给 reload_all"
    assert sent, "重载成功要广播"


async def test_reload_single_uses_account_id_lookup(monkeypatch):
    sent: list[str] = []

    async def fake_broadcast(message: str) -> None:
        sent.append(message)

    monkeypatch.setattr(modules.manager, "broadcast", fake_broadcast)
    registry = _ReloadRegistry()
    service = _BotService(registry)

    resp = await modules.reload_single_module(
        "demo", _Req(), bot_id="acc1", bot_service=service, registry=registry
    )

    assert resp.status_code == 200
    assert service.gateway.asked == ["acc1"]
    assert registry.seen == ("demo", "acc1", service.gateway.conn)


def test_resolve_bot_maps_account_id():
    gateway = _Gateway()
    assert modules.resolve_bot(gateway, "acc1") is gateway.conn
    assert modules.resolve_bot(gateway, None) is None


def test_resolve_bot_falls_back_to_legacy_gateway():
    """只有旧接口的测试替身仍可用（真实 gateway 不会走到回退）。"""

    class _LegacyGateway:
        def __init__(self) -> None:
            self.conn = object()

        def find_conn_by_bot_id(self, bot_id):
            return self.conn if bot_id == "acc1" else None

    gateway = _LegacyGateway()
    assert modules.resolve_bot(gateway, "acc1") is gateway.conn
