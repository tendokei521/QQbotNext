"""首启向导第一步：**唯一**默认账号 + 后台 2s 连接重试。

用户反馈的两个 bug 在这里锁死：
1. 以前每点一次「创建账号并连接」就多一个账号 → 现在反复保存只有一条记录；
2. 以前要手动点连接、前端还显示"第 N 次检查" → 现在后台循环重试，前端只读是否已连接。
"""

from __future__ import annotations

import asyncio

import pytest

from app.application.setup_account import (
    SetupAccountError,
    SetupAccountService,
    normalize_listen_address,
)

#: 首启向导的默认监听地址：固定 4001（端口冲突才顺延）
LISTEN_ADDRESS = "ws://127.0.0.1:4001/"


class FakeBotService:
    """记录 connect 调用，便于断言后台循环真的在重试。"""

    def __init__(self, connects_needed: int = 2, config_service=None) -> None:
        self.config_service = config_service
        self.connects_needed = connects_needed
        self.connect_calls: list[str] = []
        self.connected: set[str] = set()
        self.listening: set[str] = set()
        self.gateway = type("G", (), {"reconcile": self._reconcile})()
        self.registry = type("R", (), {"catalog_entries": staticmethod(lambda: [])})()

    async def _reconcile(self) -> None:
        return None

    def is_connected(self, account_id: str) -> bool:
        return account_id in self.connected

    def is_listening(self, account_id: str) -> bool:
        return account_id in self.listening

    def account_exists(self, account_id: str) -> bool:
        return any(str(bot.get("id")) == str(account_id) for bot in self.config_service.get_bots())

    async def add_bot(self, ws_url, owner_id, auto_connect, conn_mode="forward",
                      account_id=None, name=""):
        """替身：真正落配置的动作交给 ConfigService，与 BotService.add_bot 语义一致。"""
        created = await self.config_service.add_bot({
            "id": account_id,
            "name": name,
            "ws_url": ws_url,
            "owner_id": owner_id,
            "auto_connect": auto_connect,
            "conn_mode": conn_mode,
        })
        return created

    async def connect(self, account_id: str) -> bool:
        self.connect_calls.append(account_id)
        if len(self.connect_calls) >= self.connects_needed:
            self.connected.add(account_id)
        return True


@pytest.fixture
def service(container, config_service):
    bots = FakeBotService(config_service=config_service)
    return SetupAccountService(bots, config_service, interval=0.01), bots


def test_normalize_listen_address():
    # 反向留空 → 固定 4001 默认；正向留空保持空
    assert normalize_listen_address("") == LISTEN_ADDRESS
    # 用户自己填的地址原样保留（只补 scheme）
    assert normalize_listen_address("ws://127.0.0.1:3001/onebot/v11/ws") == (
        "ws://127.0.0.1:3001/onebot/v11/ws"
    )
    assert normalize_listen_address("127.0.0.1:3001/onebot/v11/ws") == (
        "ws://127.0.0.1:3001/onebot/v11/ws"
    )
    # 向外连接：留空保持空（不预填）
    assert normalize_listen_address("", "forward") == ""
    assert normalize_listen_address("127.0.0.1:3001/", "forward") == "ws://127.0.0.1:3001/"


async def test_upsert_creates_exactly_one_account(service, config_service):
    svc, _bots = service

    first = await svc.upsert(name="主号", ws_url=LISTEN_ADDRESS)
    second = await svc.upsert(name="改个名", ws_url="127.0.0.1:5001/ws")
    third = await svc.upsert(name="再改", ws_url="127.0.0.1:5001/ws")

    bots = config_service.get_bots()
    assert len(bots) == 1, "反复保存不能新增账号"
    assert first["account_id"] == second["account_id"] == third["account_id"]
    assert bots[0]["name"] == "再改"
    assert bots[0]["ws_url"].endswith("127.0.0.1:5001/ws")
    assert bots[0]["auto_connect"] is True
    assert bots[0]["conn_mode"] == "reverse"
    assert first["created"] is True and second["created"] is False


async def test_upsert_allows_changing_account_id(service, config_service):
    svc, bots = service
    first = await svc.upsert(account_id="default", name="主号", ws_url=LISTEN_ADDRESS)
    await config_service.save_module_config("agent", "default", {"group_enable": True})

    renamed = await svc.upsert(account_id="main", name="主号", ws_url=LISTEN_ADDRESS)

    assert first["account_id"] == "default"
    assert renamed["account_id"] == "main"
    rows = config_service.get_bots()
    assert [row["id"] for row in rows] == ["main"], "改 ID 不应留下旧行"
    assert config_service.get_module_config("agent", "main")["group_enable"] is True, "配置要跟着搬"


async def test_rename_keeps_modules_default_flag(service, config_service):
    """改 ID 不是新账号：模块默认开关要跟着搬（老账号不能被静默变成「默认关」）。"""
    svc, _bots = service
    await config_service.save_bots(
        [{"id": "old", "name": "老号", "ws_url": LISTEN_ADDRESS}]
    )
    # 模拟升级前的老账号：模块默认开
    await config_service.set_modules_default_enabled("old", True)

    renamed = await svc.upsert(account_id="renamed", name="老号")

    assert renamed["account_id"] == "renamed"
    assert config_service.get_modules_default_enabled("renamed") is True


async def test_upsert_rejects_id_already_used(service, config_service):
    svc, bots = service
    await svc.upsert(account_id="a")
    await config_service.save_bots(config_service.get_bots() + [
        {"id": "b", "name": "另一个", "ws_url": "ws://127.0.0.1:6001/ws"},
    ])

    with pytest.raises(SetupAccountError, match="已被占用"):
        await svc.upsert(account_id="b")


async def test_upsert_refuses_second_account_when_multiple_exist(service, config_service):
    svc, bots = service
    await config_service.save_bots([
        {"id": "a", "name": "A", "ws_url": "ws://127.0.0.1:1/ws"},
        {"id": "b", "name": "B", "ws_url": "ws://127.0.0.1:2/ws"},
    ])

    with pytest.raises(SetupAccountError, match="多个账号"):
        await svc.upsert()


async def test_upsert_rejects_bad_address(service):
    svc, _bots = service
    with pytest.raises(SetupAccountError, match="监听地址"):
        await svc.upsert(ws_url="ws://")


async def test_connect_loop_retries_until_connected(service):
    svc, bots = service
    result = await svc.upsert(name="主号", ws_url=LISTEN_ADDRESS)

    for _ in range(50):
        if bots.is_connected(result["account_id"]):
            break
        await asyncio.sleep(0.01)

    assert bots.is_connected(result["account_id"]), "后台循环应把账号连上"
    assert len(bots.connect_calls) >= 2, "连上之前应反复尝试（每 interval 一次）"
    assert result["connecting"] is True

    # 连上后循环自行结束
    for _ in range(50):
        if not svc.connect_loop.running(result["account_id"]):
            break
        await asyncio.sleep(0.01)
    assert svc.connect_loop.running(result["account_id"]) is False


async def test_current_returns_none_when_no_account(service):
    svc, _bots = service
    assert await svc.current() is None


async def test_current_adopts_single_existing_account(service, config_service):
    svc, _bots = service
    await config_service.save_bots([
        {"id": "only", "name": "唯一", "ws_url": "ws://127.0.0.1:7001/ws"},
    ])

    current = await svc.current()

    assert current["account_id"] == "only"
    assert current["name"] == "唯一"


async def test_forward_mode_without_address_skips_connect(service, config_service):
    """向外连接默认不填地址：可以保存账号，但不该去连空地址。"""
    svc, bots = service

    result = await svc.upsert(name="主号", conn_mode="forward", ws_url="")

    assert result["conn_mode"] == "forward"
    assert result["ws_url"] == ""
    assert result["connecting"] is False
    assert bots.connect_calls == [], "没有地址时不应发起连接"
    assert config_service.get_bots()[0]["conn_mode"] == "forward"


async def test_mode_switch_keeps_single_account(service, config_service):
    svc, bots = service
    await svc.upsert(name="主号")  # 默认本地监听
    await svc.upsert(name="主号", conn_mode="forward", ws_url="")
    await svc.upsert(name="主号", conn_mode="reverse", ws_url="")

    rows = config_service.get_bots()
    assert len(rows) == 1, "切换连接方式不能多出账号"
    assert rows[0]["conn_mode"] == "reverse"
    assert rows[0]["ws_url"].startswith("ws://127.0.0.1:4001/")


async def test_reverse_default_address_used_when_blank(service, config_service):
    svc, _bots = service
    result = await svc.upsert(name="主号", conn_mode="reverse", ws_url="")
    assert result["ws_url"] == LISTEN_ADDRESS
    assert config_service.get_bots()[0]["ws_url"].startswith("ws://127.0.0.1:4001/")


async def test_connect_loop_stops_once_reverse_listener_is_up(service):
    """反向监听一起来就停：端口常驻，继续每 2s connect 只会重复广播状态。"""
    svc, bots = service
    result = await svc.upsert(name="主号", conn_mode="reverse")

    bots.listening.add(result["account_id"])
    calls_before = len(bots.connect_calls)
    await asyncio.sleep(0.05)

    assert len(bots.connect_calls) == calls_before, "监听就绪后不该再发起连接"
    assert svc.connect_loop.running(result["account_id"]) is False


async def test_connect_loop_stops_when_account_is_deleted(service, config_service):
    """账号被删后循环要退出，不能对着不存在的账号每 2s 空转。"""
    svc, bots = service
    result = await svc.upsert(name="主号", conn_mode="forward", ws_url="ws://127.0.0.1:9/")
    await config_service.save_bots([])

    for _ in range(50):
        if not svc.connect_loop.running(result["account_id"]):
            break
        await asyncio.sleep(0.01)

    assert svc.connect_loop.running(result["account_id"]) is False


async def test_should_retry_respects_listening_connected_and_empty_address(service, config_service):
    svc, bots = service
    forward = await svc.upsert(name="正向", conn_mode="forward", ws_url="")
    assert svc.should_retry(forward["account_id"]) is False, "没有地址时不该重试"

    reverse = await svc.upsert(name="反向", conn_mode="reverse")
    assert svc.should_retry(reverse["account_id"]) is True, "反向还没监听时应重试"

    bots.listening.add(reverse["account_id"])
    assert svc.should_retry(reverse["account_id"]) is False, "监听就绪后不该再重试"

    bots.listening.discard(reverse["account_id"])
    bots.connected.add(reverse["account_id"])
    assert svc.should_retry(reverse["account_id"]) is False, "已连上不该再重试"
