"""网关的「生命周期与监督」半边：按配置收敛连接 + 掉线重连。

## 为什么单独成文件

`gateway.py` 的正文是**消息循环**（连接建立、收包、API 响应匹配、事件派发），
已经很长；而「谁该在线、掉线了要不要拉回来」是另一套节奏的状态机（10s 轮询 +
指数退避 + 僵尸扫描），两者混在一起时改轮询逻辑很容易顺手改坏收包路径。

以 mixin 形式提供（不是独立类）：监督要用网关的 `connections` / `bot_server_tasks`
/ `connect_bot` / `del_bot` / `_mark_offline` 等一堆内部状态，拆成两个对象反而
要在中间维护同步。

## 身份

一切按 **account_id**（用户设定的账号 id）对齐。历史实现按数组下标对齐，删掉中间
账号会让后面的整体前移，于是「配置里第 2 个」和「运行时第 2 个」可能不是同一个
账号——那类漂移在这个文件里已经不可能发生。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

from app.domain.bot import bot_label
from app.infrastructure.config.config_service import mask_ws_url
from app.infrastructure.onebot.endpoint import FORWARD, is_reverse, normalize_mode

#: 登录信息拉取失败后的退避重试间隔（秒）。OneBot 实现刚起/短暂报错是常态，
#: 只试一次就放弃会让连接停在「WebSocket 已连上但 runtime.online=False」的半上线态。
LOGIN_RETRY_DELAYS = (2.0, 4.0, 8.0)


class GatewayLifecycleMixin:
    """连接的收敛、监督重连与僵尸清理（由 OneBotGateway 组合使用）。"""

    # ---- 由 OneBotGateway 提供的状态（类型提示用）----
    connections: dict[str, Any]
    bot_server_tasks: dict[str, asyncio.Task]
    _supervise_task: asyncio.Task | None
    log: Any

    async def add_bot(self, *a, **kw) -> str:  # pragma: no cover
        raise NotImplementedError

    async def del_bot(self, account_id: str) -> None:  # pragma: no cover
        raise NotImplementedError

    async def connect_bot(self, account_id: str) -> bool:  # pragma: no cover
        raise NotImplementedError

    async def disconnect_bot(self, account_id: str, *, manual: bool = False) -> None:  # pragma: no cover
        raise NotImplementedError

    def _conn_mode(self, conn) -> str:  # pragma: no cover
        raise NotImplementedError

    def _reverse_listening(self, account_id: str | None) -> bool:  # pragma: no cover
        raise NotImplementedError

    async def _mark_offline(self, conn, reason: str) -> None:  # pragma: no cover
        raise NotImplementedError

    async def _get_login_info(self, conn) -> bool:  # pragma: no cover
        raise NotImplementedError

    async def _get_login_groups(self, conn) -> bool:  # pragma: no cover
        raise NotImplementedError

    @staticmethod
    def _reset_conn_state(conn) -> None:  # pragma: no cover
        raise NotImplementedError

    # ==================== 收敛 ====================
    async def start_all(self, bots_config: list[dict]) -> None:
        """按配置收敛连接列表，并行连接 auto_connect 账号，启动监督循环。"""
        await self._reconcile(bots_config)
        # 监督循环**必须**先起来：下面任一 connect 抛异常都不能让自动重连整体失效
        self._ensure_supervise_task()
        auto_ids = [
            aid for aid, conn in list(self.connections.items())
            if conn.auto_connect and conn.status not in ("connected", "connecting")
        ]
        if auto_ids:
            # 单账号握手失败（端口拒绝/地址非法）只影响它自己，不阻断其它账号启动
            results = await asyncio.gather(
                *(self.connect_bot(aid) for aid in auto_ids), return_exceptions=True
            )
            for account_id, result in zip(auto_ids, results, strict=True):
                if isinstance(result, BaseException):
                    self.log.error(f"账号「{account_id}」启动连接异常: {result}")

    def _ensure_supervise_task(self) -> None:
        """确保监督循环在跑（幂等；供 start_all 与外部恢复入口复用）。"""
        task = self._supervise_task
        if task is None or task.done():
            self._supervise_task = asyncio.create_task(self._supervise(), name="gateway_supervise")

    async def reconcile(self) -> None:
        """按当前配置列表立即收敛连接映射。

        WebUI 在增/删账号后主动调用，避免依赖监督循环（10s 周期）造成的窗口。
        """
        getter = getattr(self, "bots_provider", None)
        bots_config = getter() if getter else []
        await self._reconcile(bots_config)

    async def _reconcile(self, bots_config: list[dict]) -> None:
        """按配置收敛 gateway 连接映射（以 account_id 对齐，不再有下标漂移）。

        - 配置里已不存在的账号 → 断开并移除；
        - 新出现 → 建连接对象（不自动连，等 connect/supervise）；
        - 已存在 → 同步 name/ws_url/auto_connect/conn_mode，地址或方向变更则断开待重连。
        """
        wanted = {str(cfg["id"]): cfg for cfg in bots_config if cfg.get("id")}
        for account_id in list(self.connections.keys()):
            if account_id not in wanted:
                await self.del_bot(account_id)
        for account_id, cfg in wanted.items():
            ws_url = cfg.get("ws_url", "")
            name = cfg.get("name", "") or account_id
            conn = self.connections.get(account_id)
            if conn is None:
                await self.add_bot(
                    ws_url, None, cfg.get("auto_connect", False), account_id=account_id,
                    conn_mode=cfg.get("conn_mode", FORWARD), name=name,
                )
                continue
            changed = False
            if name != conn.account_name:
                # 改名只需同步展示名，不必重连（id 没变，一切 key 都还指得对）
                conn.account_name = name
            if cfg.get("ws_url") != conn.ws_url:
                conn.ws_url = ws_url
                changed = True
            # 连接方向变更（正向 ⇄ 反向）必须断开重建：正向是「连出去」，反向是「起监听」，
            # 不复位就会出现「改了模式但旧监听还在跑、新地址没人听」。
            mode = normalize_mode(cfg.get("conn_mode", self._conn_mode(conn)))
            if mode != self._conn_mode(conn):
                conn.conn_mode = mode
                changed = True
            # 注意：**不要**再比较 owner_id。
            # v3 起账号级 owner_id 已废弃（拥有者身份只来自「权限管理 → 名册」），
            # `conn.owner_id` 恒为 None；若仍拿配置里的旧值去比，会永远"不相等"，
            # 于是每 10 秒的监督轮询都判定"配置已更新"→ 断开重连，形成死循环。
            if bool(cfg.get("auto_connect", False)) != conn.auto_connect:
                # 重新打开「自动连接」开关 = 明确要它在线 → 解除手动断开的抑制
                if cfg.get("auto_connect") and not conn.auto_connect:
                    conn.suppress_auto_reconnect = False
                conn.auto_connect = bool(cfg.get("auto_connect", False))
                changed = True
            if changed:
                self.log.info(f"账号「{bot_label(conn)}」配置已更新: {mask_ws_url(conn.ws_url)}")
                if conn.status in ("connected", "connecting") or self._reverse_listening(account_id):
                    await self.disconnect_bot(account_id)  # url/方向变更 → 断开待重连(或待重新监听)

    # ==================== 登录信息拉取 ====================
    def _schedule_login(self, conn: Any, key: str, bot_logger: Any, login_tasks: list[asyncio.Task]) -> None:
        """按需启动登录信息拉取（每连接同一时刻只允许一个拉取任务）。

        已经取到（``login_retrieved``）→ 不再拉；上一轮重试任务还在跑 → 不叠加。
        上一轮因 OneBot 实现暂时不可用而全部失败后，下一条正常消息会重新拉起一轮，
        直到取到为止（配合 :meth:`_login_with_retry` 的退避重试）。
        """
        if getattr(conn, "login_retrieved", False):
            return
        if login_tasks and not login_tasks[-1].done():
            return
        login_tasks.clear()  # 丢弃已完成任务的引用，避免列表随消息增长
        login_tasks.append(asyncio.create_task(
            self._login_with_retry(conn, bot_logger), name=f"login:{key}"
        ))

    async def _login_with_retry(self, conn: Any, bot_logger: Any) -> bool:
        """拉取登录信息并在失败时退避重试；只有成功后才把连接标为「已取过」。

        历史缺陷：``_get_login_info`` 失败一次就直接放弃，且「已尝试」标记在成败
        之前就已置位 → 之后永不重试。表现为 WebUI 显示已连接，但 ``runtime.online``
        恒为 False：定时任务、主动消息、模块 ``on_online`` 全部不工作。
        """
        delays = LOGIN_RETRY_DELAYS
        for attempt in range(1 + len(delays)):
            if getattr(conn, "login_retrieved", False):
                return True
            try:
                if await self._get_login_info(conn):
                    conn.login_retrieved = True
                    # 群列表只用于日志展示与群名查找，失败不影响上线；登录成功后补取一次
                    if not conn.all_group_list:
                        await self._get_login_groups(conn)
                    return True
            except asyncio.CancelledError:
                raise
            except Exception as e:  # _get_login_info 内部已兜异常，这里防御性留痕
                bot_logger.warning(f"[Gateway] 登录信息拉取异常（第 {attempt + 1} 次）: {e}")
            if attempt >= len(delays):
                break
            bot_logger.warning(
                f"账号「{bot_label(conn)}」未取到登录信息，{delays[attempt]:g}s 后重试"
            )
            await asyncio.sleep(delays[attempt])
        bot_logger.error(
            f"账号「{bot_label(conn)}」多次重试仍未取到登录信息：连接保持，"
            "下一条事件到达时会再试（定时任务/主动消息在取到前不启动）"
        )
        return False

    # ==================== 监督 ====================
    async def _supervise(self) -> None:
        """定期收敛连接映射，并以指数退避重连掉线的 auto_connect 账号。"""
        while True:
            await asyncio.sleep(10)
            getter = getattr(self, "bots_provider", None)
            if not getter:
                continue
            try:
                bots_config = getter()  # 同步 provider（ConfigService.get_bots）
                await self._reconcile(bots_config)
                await self._sweep_zombies()
                await self._reconnect_due()
            except Exception as e:
                self.log.error(f"监督循环异常: {e}")

    async def _reconnect_due(self) -> None:
        """把到期的掉线账号**并发**重连（并发上限由 connect_bot 的 Semaphore 控制）。

        旧实现逐个 ``await connect_bot``：N 个账号同时掉线时，第 N 个要排队等前
        N-1 个握手完成（每个最长数秒），表现为「掉线后几十秒才开始连一部分账号」。
        """
        due: list[str] = []
        for account_id in list(self.connections.keys()):
            conn = self.connections[account_id]
            if not conn.auto_connect or conn.status in ("connected", "connecting"):
                continue
            if getattr(conn, "suppress_auto_reconnect", False):
                # 用户主动断开：只保留「意外掉线」的自动恢复语义，不把它的断开连回来
                continue
            if is_reverse(self._conn_mode(conn)) and self._reverse_listening(account_id):
                # 反向：监听还在，只是暂时没有对端接入。**不要**重开端口
                # （会踢掉正在重连的 OneBot 实现），把状态纠正为「等待接入」即可。
                await self._mark_offline(conn, conn.last_error or "等待 OneBot 实现接入")
                continue
            # 指数退避：10s → 20s → 40s → 80s → 160s → 300s 封顶
            backoff = min(10 * (2 ** min(conn.reconnect_attempts, 4)), 300)
            last = getattr(conn, "_last_connect_attempt", 0.0)
            if time.time() - last < backoff:
                continue
            conn._last_connect_attempt = time.time()
            due.append(account_id)
        if not due:
            return
        # 单账号握手异常不得中断其它账号的重连（return_exceptions 兜住并逐个记录）
        results = await asyncio.gather(
            *(self.connect_bot(aid) for aid in due), return_exceptions=True
        )
        for account_id, result in zip(due, results, strict=True):
            if isinstance(result, BaseException):
                self.log.error(f"账号「{account_id}」重连异常: {result}")

    async def _sweep_zombies(self) -> None:
        """把「状态还写着 connected、连接其实已经没了」的账号改成掉线态，交给重连逻辑。

        正常收尾（``_handle_connection_end`` / ``disconnect_bot``）都会改状态；
        这里兜住没走到收尾的静默路径（接收任务消失、ws 已被对端关闭等）——
        否则监督循环会把它们当成「在线」，永不重连，表现为静默死亡。
        """
        for account_id, conn in list(self.connections.items()):
            if conn.status != "connected" or not self._connection_gone(account_id, conn):
                continue
            self.log.warning(
                f"账号「{bot_label(conn)}」连接已不存在但状态仍是 connected，按掉线处理"
            )
            await self._mark_offline(conn, "连接已不存在（僵尸连接，已按掉线处理）")
            if conn.websocket is not None:
                with contextlib.suppress(Exception):
                    await conn.websocket.close()
            conn.websocket = None
            self._reset_conn_state(conn)

    def _connection_gone(self, account_id: str, conn) -> bool:
        """connected 状态下「连接已消失」的判定（任一命中即视为已消失）。

        调用方（``_sweep_zombies``）已限定只看 status=="connected"：握手中的
        "connecting" 期间 websocket 尚未赋值、任务尚未创建，用本判据会被误判。
        """
        if conn.websocket is None:
            return True
        # 对端关闭后 websockets 会写入 close_code；收尾若没走到，这里能兜住
        if getattr(conn.websocket, "close_code", None) is not None:
            return True
        task = self.bot_server_tasks.get(str(account_id))
        return task is None or task.done()
