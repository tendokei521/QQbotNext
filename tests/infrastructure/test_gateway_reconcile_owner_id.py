"""`_reconcile` 的"配置已更新"判定：不许因为字段废弃而每轮误判。

## 这个 bug 的现场

```
13:10:42 配置已更新 → 断开 → 连接成功
13:10:52 配置已更新 → 断开 → 连接成功   ← 每 10 秒一次，无限重连
```

根因：v3 把账号级 ``owner_id`` 废弃后，``BotConnection.owner_id`` 恒为 ``None``，
而 `_reconcile` 仍拿 DB 里的旧 ``owner_id``（非空）去比较：

```python
if cfg.get("owner_id") != conn.owner_id:   # 123 != None → 永远成立
    conn.owner_id = cfg.get("owner_id")    # 写入被忽略（property 无 setter 存储）
    changed = True                         # → 每轮都"配置已更新"→ 断开重连
```

现在 `_reconcile` 不再比较 ``owner_id``。本文件锁住三条：
1. 配置里还留着旧 ``owner_id`` 时，**不得**判定为发生变化；
2. `ws_url` / `auto_connect` 真变化时仍然要判定变化并断开；
3. 反复 reconcile（模拟 10s 轮询多次）**不得**产生任何断开。
"""

from __future__ import annotations

import pytest

from app.infrastructure.cache import Cache
from app.infrastructure.onebot.gateway import OneBotGateway


class _Settings:
    ws_connect_timeout = 5


@pytest.fixture
def gateway():
    gw = OneBotGateway(settings=_Settings(), cache=Cache())
    gw.disconnect_calls = []

    async def _fake_disconnect(index):
        gw.disconnect_calls.append(index)

    gw.disconnect_bot = _fake_disconnect  # type: ignore[assignment]
    return gw


async def _add_connected(gateway, ws_url="ws://127.0.0.1:3001", auto_connect=False):
    await gateway.add_bot(ws_url, None, auto_connect, index=0)
    conn = gateway.connections[0]
    conn.status = "connected"
    return conn


async def test_legacy_owner_id_in_config_does_not_trigger_update(gateway):
    """回归：配置里残留 owner_id 时不得判定"配置已更新"（否则 10s 一轮重连）。"""
    await _add_connected(gateway)

    await gateway._reconcile([{"ws_url": "ws://127.0.0.1:3001", "owner_id": 123, "auto_connect": False}])

    assert gateway.disconnect_calls == [], "残留 owner_id 不该触发断开重连"
    assert gateway.connections[0].status == "connected"


async def test_repeated_reconcile_is_idempotent(gateway):
    """模拟监督循环跑 5 轮：不能有任何一轮判定为变化。"""
    await _add_connected(gateway)
    bots = [{"ws_url": "ws://127.0.0.1:3001", "owner_id": 999, "auto_connect": False}]

    for _ in range(5):
        await gateway._reconcile(bots)

    assert gateway.disconnect_calls == []


async def test_ws_url_change_still_disconnects(gateway):
    """真变化仍然要断开（不要在修 bug 时把正常能力一起关掉）。"""
    await _add_connected(gateway)

    await gateway._reconcile([{"ws_url": "ws://10.0.0.2:3001", "owner_id": 123}])

    assert gateway.disconnect_calls == [0]
    assert gateway.connections[0].ws_url == "ws://10.0.0.2:3001"


async def test_auto_connect_change_still_disconnects(gateway):
    await _add_connected(gateway, auto_connect=False)

    await gateway._reconcile([{"ws_url": "ws://127.0.0.1:3001", "auto_connect": True}])

    assert gateway.disconnect_calls == [0]
    assert gateway.connections[0].auto_connect is True


async def test_owner_id_property_is_read_only_none(gateway):
    """账号级 owner_id 已是只读 None：写入被忽略但不抛异常。"""
    conn = await _add_connected(gateway)
    assert conn.owner_id is None
    conn.owner_id = 456  # 旧插件可能这么写
    assert conn.owner_id is None


async def test_add_bot_ignores_legacy_owner_id_argument(gateway):
    """兼容参数：仍可传 owner_id，但不该被保存（也不该报错）。"""
    await gateway.add_bot("ws://127.0.0.1:3002", 777, True, index=1)
    conn = gateway.connections[1]
    assert conn.owner_id is None
    assert conn.auto_connect is True
