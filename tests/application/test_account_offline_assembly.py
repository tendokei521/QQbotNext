"""离线可用性：账号存在即可编辑配置，**但离线绝不跑任务**。

v4 之后账号 id 在「添加账号」时就确定且不可变，配置归属不再依赖登录成功。旧设计把
"运行时装配"挂在登录回调上，导致未连接的账号：Agent 面板整页 404、模块配置静默回退到
全局实例、人格"应用"按钮永久灰。本文件把这些新契约钉死：

1. 建号即装配运行时与模块实例，`runtime.online is False`；
2. 离线时配置类接口（Agent 配置 / agent 虚拟模块配置）全部可用；
3. 离线时定时任务与主动消息的计时器**一个都不武装**；
4. 上线才武装，断线立刻停；
5. 删号回收运行时与模块实例（不留幽灵）。
"""

from __future__ import annotations

import pytest

from app.agent.runtime import AgentManager
from app.application.bot_service import BotService
from app.plugin.registry import ModuleRegistry


class _FakeConn:
    """够用的连接替身：`on_bot_login` 只用到 account_id / label。"""

    account_id = ""
    account_name = ""
    bot_id = None

    def __init__(self, account_id: str) -> None:
        self.account_id = account_id

    @property
    def label(self) -> str:
        return self.account_id


async def _add_account(bot_service, account_id: str = "acc_offline") -> str:
    return await bot_service.add_bot(
        ws_url="ws://127.0.0.1:39999", owner_id=None, auto_connect=False,
        account_id=account_id, name="离线账号",
    )


async def test_creating_account_assembles_runtime_offline(container):
    """建号就把运行时与模块实例装配好——不需要连接。"""
    bot_service = container.get(BotService)
    manager = container.get(AgentManager)

    account_id = await _add_account(bot_service)

    runtime = manager.get_runtime(account_id)
    assert runtime is not None, "账号一创建就该有运行时（配置需要归属）"
    assert runtime.online is False, "没连上 QQ，运行时应为离线"
    assert container.get(ModuleRegistry).account_modules(account_id), "该账号的模块应已装配"


async def test_agent_config_editable_without_connection(container):
    """Agent 配置读写不依赖连接：GET/POST 都该 200，并真的落库。"""
    from starlette.testclient import TestClient

    from app.webui.app import create_app

    client = TestClient(create_app(container))
    bot_service = container.get(BotService)
    account_id = await _add_account(bot_service, "acc_edit")

    got = client.get("/api/agent/config", params={"account_id": account_id})
    assert got.status_code == 200, got.text
    assert got.json()["bot_id"] == account_id

    posted = client.post(
        "/api/agent/config",
        params={"account_id": account_id},
        json={"config": {"system_prompt": "离线写进去的人设"}},
    )
    assert posted.status_code == 200, posted.text

    again = client.get("/api/agent/config", params={"account_id": account_id})
    assert again.json()["config"]["system_prompt"] == "离线写进去的人设"


async def test_agent_virtual_module_config_without_connection(container):
    """`/api/module/agent/*` 走 `_AgentProxy`，账号存在即可用。"""
    from starlette.testclient import TestClient

    from app.webui.app import create_app

    client = TestClient(create_app(container))
    bot_service = container.get(BotService)
    account_id = await _add_account(bot_service, "acc_proxy")

    resp = client.get("/api/module/agent/config", params={"account_id": account_id})
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True


async def test_unknown_account_is_404(container):
    """账号真的不存在时才 404——消息要说清是账号问题，而不是"没有运行时"。"""
    from starlette.testclient import TestClient

    from app.webui.app import create_app

    client = TestClient(create_app(container))

    resp = client.get("/api/agent/config", params={"account_id": "nobody"})
    assert resp.status_code == 404
    assert "nobody" in resp.json()["message"]


async def test_offline_account_arms_no_timers(container):
    """**离线不跑任务**：定时任务/主动消息管理器都在，但计时器一个都不许武装。"""
    bot_service = container.get(BotService)
    account_id = await _add_account(bot_service, "acc_idle")
    runtime = container.get(AgentManager).get_runtime(account_id)

    scheduler = getattr(runtime, "scheduler", None)
    proactive = getattr(runtime, "proactive_service", None)
    # 管理器由模块 on_load 装配（配置页要用），但必须是"未启动"状态
    assert scheduler is not None, "定时任务模块应已装配（面板可查看任务）"
    assert scheduler._running is False
    assert scheduler._timers == {}
    if proactive is not None:
        assert proactive._running is False
        assert proactive._timers == {}


async def test_going_online_starts_timers_and_offline_stops_them(container):
    """上线武装、断线停止：`on_bot_login` / `on_bot_status` 是这两个开关。"""
    bot_service = container.get(BotService)
    account_id = await _add_account(bot_service, "acc_net")
    runtime = container.get(AgentManager).get_runtime(account_id)
    scheduler = getattr(runtime, "scheduler", None)
    assert scheduler is not None

    await bot_service.on_bot_login(_FakeConn(account_id))
    assert runtime.online is True
    assert scheduler._running is True

    await bot_service.on_bot_status(_FakeConn(account_id), "disconnected")
    assert runtime.online is False
    assert scheduler._running is False, "断线后必须停表，否则会对着死连接继续发"


async def test_deleting_account_reclaims_runtime_and_modules(container):
    """删号回收运行时与模块实例（历史遗留：删完还留着幽灵运行时）。"""
    bot_service = container.get(BotService)
    manager = container.get(AgentManager)
    registry = container.get(ModuleRegistry)
    account_id = await _add_account(bot_service, "acc_gone")
    assert manager.get_runtime(account_id) is not None

    await bot_service.delete_bot(account_id)

    assert manager.get_runtime(account_id) is None
    assert registry.account_modules(account_id) == []


@pytest.mark.parametrize("state", ["connected", "login", "reconnecting"])
async def test_non_offline_states_do_not_stop_timers(container, state):
    """只有 disconnected / error 才停任务；连接途中的状态不能误停。"""
    bot_service = container.get(BotService)
    account_id = await _add_account(bot_service, f"acc_state_{state}")
    runtime = container.get(AgentManager).get_runtime(account_id)
    scheduler = getattr(runtime, "scheduler", None)

    await bot_service.on_bot_login(_FakeConn(account_id))
    await bot_service.on_bot_status(_FakeConn(account_id), state)

    assert runtime.online is True
    assert scheduler._running is True
