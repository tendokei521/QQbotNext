"""网关与事件总线的「顺手项」回归测试。

覆盖四个小但会静默出错的点：
1. ``get_bot_id`` 不能再 100×0.1s 轮询（白耗 CPU、精度差）→ 登录就绪事件唤醒；
2. ``EventBus.publish`` 迭代订阅表时必须用快照（处理器内 subscribe 会抛
   "dictionary changed size during iteration"）；
3. 状态去重键要含 detail（同状态换原因也要广播，否则前端停在旧详情）；
4. 忽略计数缓存键要含 account_id（否则 A 账号的忽略会让 B 账号的消息被吞）。
"""

from __future__ import annotations

import asyncio

from app.core.event_bus import BotLifecycleEvent, ConfigChangedEvent, EventBus, event_bus
from app.domain.events import GroupMessageEvent
from app.infrastructure.cache import Cache
from app.infrastructure.onebot.client import BotConnection
from app.infrastructure.onebot.gateway import OneBotGateway


class _Settings:
    ws_ping_interval = 30
    ws_ping_timeout = 10
    ws_connect_timeout = 3


def _gateway() -> OneBotGateway:
    return OneBotGateway(settings=_Settings(), cache=Cache())


def _conn() -> BotConnection:
    conn = BotConnection()
    conn.account_id = "main"
    conn.account_name = "主号"
    return conn


def _event(account_id: str, user_id: int = 100) -> GroupMessageEvent:
    return GroupMessageEvent(
        event_type="message_group", message_type="group", time=1,
        user_id=user_id, self_id=1, message=[],
        group=type("G", (), {"group_id": 999})(),
        account_id=account_id,
    )


# ---------- get_bot_id：事件唤醒而不是轮询 ----------


async def test_get_bot_id_wakes_up_when_login_completes():
    gw = _gateway()
    conn = _conn()
    gw.connections = {"main": conn}

    task = asyncio.create_task(gw.get_bot_id("main"))
    await asyncio.sleep(0.05)
    assert not task.done(), "未登录时 get_bot_id 应等待登录就绪信号"

    conn.bot_id = 987654
    conn.login_event.set()

    assert await asyncio.wait_for(task, 1) == 987654


async def test_get_bot_id_returns_immediately_when_already_logged_in():
    gw = _gateway()
    conn = _conn()
    conn.bot_id = 10001
    gw.connections = {"main": conn}

    assert await asyncio.wait_for(gw.get_bot_id("main"), 0.5) == 10001
    assert await gw.get_bot_id("missing") is None


# ---------- EventBus：发布期间订阅表变化 ----------


async def test_publish_uses_snapshot_when_handler_subscribes():
    """处理器内注册新订阅者不得让 publish 抛字典尺寸变化异常。"""
    bus = EventBus()
    calls: list[str] = []

    async def late(_event):
        calls.append("late")

    async def first(_event):
        calls.append("first")
        bus.subscribe(ConfigChangedEvent, late)

    bus.subscribe(ConfigChangedEvent, first)
    await bus.publish(ConfigChangedEvent("bots"))

    assert calls == ["first"], "本轮发布只应命中快照里的订阅者"
    assert len(bus._subscribers[ConfigChangedEvent]) == 2


# ---------- 状态去重键：(state, detail) ----------


async def test_status_broadcast_dedup_includes_detail():
    gw = _gateway()
    conn = _conn()
    conn.account_id = "acc0"
    states: list[tuple[str, str]] = []

    async def on_event(event):
        if event.account_id == "acc0":
            states.append((event.state, event.detail))

    event_bus.subscribe(BotLifecycleEvent, on_event)
    try:
        await gw._notify_status(conn, "connected", "等待接入")
        await gw._notify_status(conn, "connected", "等待接入")
        assert states == [("connected", "等待接入")], "完全相同 (state, detail) 才去重"

        await gw._notify_status(conn, "connected", "已登录")
        assert states[-1] == ("connected", "已登录"), "状态相同但 detail 变化必须广播"
        assert len(states) == 2
    finally:
        event_bus.unsubscribe(BotLifecycleEvent, on_event)


# ---------- 忽略计数：按账号隔离 ----------


async def test_ignore_marker_is_per_account():
    gw = _gateway()
    event_a = _event("a")
    event_b = _event("b")

    gw.cache.set("a_message_group_100_ignore", 1, 60)
    assert gw._is_ignore(event_a) is True
    assert gw._is_ignore(event_b) is False, "A 账号的忽略计数不得影响 B 账号（旧键会串味）"


async def test_ignore_marker_legacy_key_still_honored():
    """第三方插件仍写历史键（不带 account_id）时保持兼容。"""
    gw = _gateway()
    gw.cache.set("message_group_100_ignore", 1, 60)
    assert gw._is_ignore(_event("b")) is True
