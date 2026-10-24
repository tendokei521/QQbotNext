"""反向连接的账号级生命周期（网关的反向半边）。

## 为什么单独成文件

网关要同时管两个方向的连接，而这两个方向的状态机形状不同：

- 正向：``connect_bot`` 连出去 → 成功/失败立刻已知 → 掉线靠监督循环重连；
- 反向：``connect_bot`` 只是**起监听** → 「成功」仅代表端口就绪，真正的上线发生在
  OneBot 实现接进来那一刻 → 对端断线监听仍然在，不该判死。

把反向这半边的状态机（监听器表、接入、等待接入的状态收敛）收在这里，网关那边
只留一行分支，也让 ``gateway.py`` 保持在文件长度闸门（800 行）之内。
两边共用的仍是同一套 ``BotConnection`` 与 ``_bot_server`` 消息循环——
反向连接在消息语义上与正向完全一致。

## key 与文案

所有表都以 **account_id**（用户设定的账号 id）为 key，绝不用数组下标。
日志一律打**账号名**：id 是给程序判断用的，不该出现在用户读的日志里。
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import time
from typing import Any

from app.domain.bot import bot_label
from app.infrastructure.config.config_service import mask_ws_url
from app.infrastructure.onebot.reverse import CLOSE_REPLACED, ReverseClient, ReverseWsServer


def _label(conn: Any) -> str:
    """账号展示名（日志用）。"""
    return bot_label(conn)


class ReverseLink:
    """管理全部账号的反向 WS 监听器。

    ``host`` 是网关本体（需要它的 ``connections`` / ``bot_server_tasks`` /
    ``_bot_server`` / ``_notify_status`` 与 ``settings``）：反向连接最终仍然跑网关
    那条消息循环，这里只负责「什么时候监听、谁接进来、状态怎么说」。
    """

    def __init__(self, host: Any) -> None:
        self._host = host
        self.servers: dict[str, ReverseWsServer] = {}
        # 停止监听时连接对象可能已被移除，先留一份名字用于日志
        self._labels: dict[str, str] = {}

    # ==================== 查询 ====================
    def listening(self, account_id: str | None) -> bool:
        if account_id is None:
            return False
        server = self.servers.get(str(account_id))
        return bool(server is not None and server.listening)

    def listen_url(self, account_id: str) -> str:
        server = self.servers.get(str(account_id))
        return server.listen_url if server else ""

    # ==================== 监听生命周期 ====================
    async def ensure_listening(self, account_id: str, conn) -> bool:
        """起（或复用）该账号的监听器；返回「监听是否已就绪」。

        与正向不同，返回 True 时状态是 ``connecting``（等对端接入），
        监督循环据此不会反复重开端口——反复重开会把正在重连的 OneBot 实现踢掉。
        """
        host = self._host
        key = str(account_id)
        label = _label(conn)
        self._labels[key] = label
        server = self.servers.get(key)
        if server is not None and server.listening and server.url == conn.ws_url:
            await host._notify_status(conn, conn.status, "等待接入")
            return True

        if server is not None:
            await self.stop(key)
        try:
            server = ReverseWsServer(
                url=conn.ws_url,
                handler=functools.partial(self._on_client, key),
                log=_scoped_log(host, label),
                ping_interval=getattr(host.settings, "ws_ping_interval", None),
                ping_timeout=getattr(host.settings, "ws_ping_timeout", None),
            )
            await server.start()
        except Exception as e:
            # 地址非法 / 端口被占用 / 无权限绑定：都是用户可修的错误，落到 last_error
            conn.reconnect_attempts += 1
            conn.status = "error"
            conn.last_error = f"反向监听失败: {e}"
            conn._last_connect_attempt = time.time()
            host.log.error(f"账号「{label}」反向监听失败: {e} ({mask_ws_url(conn.ws_url)})")
            await host._notify_status(conn, "error", conn.last_error)
            return False

        self.servers[key] = server
        conn.last_error = None
        conn.status = "connecting"
        host.log.info(
            f"账号「{label}」反向 WS 监听中: {server.listen_url}"
            "（在 OneBot 实现里把反向 WebSocket 指向该地址）"
        )
        await host._notify_status(conn, "connecting", "等待 OneBot 实现接入")
        return True

    async def stop(self, account_id: str) -> None:
        """停止该账号的监听（用户断开 / 账号删除 / 地址变更时调用）。"""
        key = str(account_id)
        server = self.servers.pop(key, None)
        label = self._labels.pop(key, key)
        if server is None:
            return
        await server.stop()
        self._host.log.info(f"账号「{label}」反向 WS 监听已停止: {server.listen_url}")

    async def stop_all(self) -> None:
        for account_id in list(self.servers):
            await self.stop(account_id)

    async def mark_waiting(self, conn, reason: str) -> bool:
        """反向且监听仍在 → 状态记「等待接入」（对端重启是常态，不是错误）。

        返回是否接管了这次状态变更；``False`` 表示调用方应按普通掉线处理。
        """
        if not self.listening(getattr(conn, "account_id", None)):
            return False
        conn.status = "connecting"
        conn.last_error = reason
        await self._host._notify_status(conn, "connecting", reason)
        return True

    # ==================== 接入 ====================
    async def _on_client(self, account_id: str, websocket, client: ReverseClient) -> None:
        """OneBot 实现接进来了：挂到账号上，然后跑与正向相同的消息循环。

        本协程被监听器 await 到连接结束为止——websockets 在 handler 返回时就关闭
        连接，提前返回等于把网关的读循环掐死。
        """
        host = self._host
        conn = host.connections.get(account_id)
        if conn is None:
            label = self._labels.get(account_id, account_id)
            host.log.warning(f"[Gateway] 反向连接指向已删除的账号「{label}」，已关闭")
            with contextlib.suppress(Exception):
                await websocket.close(code=4001, reason="账号不存在")
            return

        label = _label(conn)
        # 同一账号只允许一条反向连接。OneBot 实现重启时常见「旧连接尚未超时、
        # 新连接已到」，两条读循环并存会把每条事件派发两次（群里会看到重复回复）。
        # 新连接优先：先接管，再关旧连接——旧循环的收尾会因 identity 不匹配而跳过。
        previous = conn.websocket
        conn.websocket = websocket
        if previous is not None and previous is not websocket:
            host.log.info(f"账号「{label}」旧反向连接已被新接入替换，正在关闭旧连接")
            with contextlib.suppress(Exception):
                await previous.close(code=CLOSE_REPLACED, reason="被新连接替换")

        conn.status = "connected"
        conn.reconnect_attempts = 0
        conn.last_error = None
        conn.suppress_auto_reconnect = False
        host.log.info(
            f"账号「{label}」反向连接已接入: self_id={client.self_id or '?'} "
            f"role={client.role or 'Universal'} 来自 {client.peer or '?'}"
        )
        await host._notify_status(conn, "connected", "反向连接已接入")

        task = asyncio.create_task(
            host._bot_server(account_id, login_immediately=True), name=f"bot_server:{account_id}"
        )
        host.bot_server_tasks[account_id] = task
        try:
            await task
        except asyncio.CancelledError:
            raise
        finally:
            # 只清理自己的登记项：被替换后这一格已经是别的任务
            if host.bot_server_tasks.get(account_id) is task:
                host.bot_server_tasks.pop(account_id, None)


def _scoped_log(host: Any, label: str):
    """监听器日志带上账号名前缀（与网关内其它日志同一套前缀规则）。"""
    add_info = getattr(host.log, "add_info", None)
    return add_info(label) if add_info else host.log
