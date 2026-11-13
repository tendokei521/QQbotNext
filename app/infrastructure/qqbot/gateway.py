"""官方 QQ 机器人网关：账号生命周期（连接、重连、状态广播、事件派发）。

与 ``OneBotGateway`` 的对外形状保持一致（``connections`` / ``add_bot`` /
``connect_bot`` / ``get_bot_info`` / ``recent_events`` …），因此
``GatewayHub`` 可以把两种协议拼成同一张账号表，``BotService`` / WebUI 不用区分协议。

差异只在传输：

- 官方账号**没有反向监听**（永远由框架连官方网关）；
- 事件走 WS、发送走 HTTPS（连接对象里各有一条链路）；
- 没有 ``get_login_info`` 轮询：``READY`` 事件就是登录成功。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.event_bus import BotLifecycleEvent, event_bus
from app.core.logger import logger
from app.domain.bot import bot_label
from app.infrastructure.onebot.gateway_events import EventHistory
from app.infrastructure.onebot.log_text import received_log_lines
from app.infrastructure.qqbot.codec import decode_event, dedupe_key, group_of, message_target
from app.infrastructure.qqbot.connection import QqBotConnection
from app.infrastructure.qqbot.ws import QqBotReconnect

LoginHandler = Callable[[QqBotConnection], Awaitable[None]]
AccountSaver = Callable[[str, Any, str], Awaitable[bool]]

#: 官方账号的监督轮询间隔（秒）：掉线自动重连、auto_connect 新账号拉起
SUPERVISE_INTERVAL = 10.0
#: 重连退避上限（秒）
MAX_BACKOFF = 60.0
#: 每个账号的事件队列上限（超出丢最旧，避免 LLM 慢时把内存吃光）
EVENT_QUEUE_MAX = 200


class QqBotGateway:
    def __init__(
        self,
        *,
        settings: Any = None,
        cache: Any = None,
        logger_: Any = None,
        login_handler: LoginHandler | None = None,
    ) -> None:
        self.settings = settings
        self.cache = cache
        self.log = logger_ or logger
        self.login_handler = login_handler

        self.connections: dict[str, QqBotConnection] = {}
        self.bot_server_tasks: dict[str, asyncio.Task] = {}
        self._connect_locks: dict[str, asyncio.Lock] = {}
        self._supervise_task: asyncio.Task | None = None
        # Webhook 模式：没有读循环，事件由回调路由推进来；每账号一条队列 + 一个 worker
        self._webhook_queues: dict[str, asyncio.Queue] = {}
        self._webhook_workers: dict[str, asyncio.Task] = {}

        # 由 BotService / bootstrap 注入（与 OneBotGateway 同名）
        self.bots_provider: Callable[[], list[dict]] | None = None
        self.status_handler: Callable[[Any, str, str], Awaitable[None]] | None = None
        self.dispatch_handler: Callable[[Any], Awaitable[None]] | None = None
        self.account_saver: AccountSaver | None = None
        self.account_lookup: Callable[[], dict] | None = None
        self.outbound_hook_factory = None
        self.send_hook_registry = None
        self.before_send_hook_registry = None
        self.api_hook_registry = None
        self.lifecycle_hook_registry = None
        self.event_history = EventHistory()
        self.event_queue_drops: dict[str, int] = {}
        #: (account_id, group_openid)：群信息接口不可用过的群，避免每条消息都重试
        self._group_name_denied: set[tuple[str, str]] = set()

    # ==================== 账号表 ====================
    def _configs(self) -> list[dict]:
        rows = self.bots_provider() if self.bots_provider else []
        return [
            row for row in (rows or [])
            if str((row or {}).get("protocol") or "onebot") == "qqbot"
        ]

    async def start_all(self, bots_config: list[dict]) -> None:
        """按配置收敛账号表，并行连接 auto_connect 账号，启动监督循环。"""
        await self.reconcile()
        self._ensure_supervise_task()
        auto_ids = [
            aid for aid, conn in list(self.connections.items())
            if conn.auto_connect and conn.status not in ("connected", "connecting")
        ]
        if auto_ids:
            results = await asyncio.gather(
                *(self.connect_bot(aid) for aid in auto_ids), return_exceptions=True
            )
            for account_id, result in zip(auto_ids, results, strict=True):
                if isinstance(result, BaseException):
                    self.log.error(f"官方账号「{account_id}」启动连接异常: {result}")

    async def reconcile(self) -> None:
        """按当前配置收敛连接映射（只处理 protocol=qqbot 的账号）。"""
        wanted = {str(cfg["id"]): cfg for cfg in self._configs() if cfg.get("id")}
        for account_id in list(self.connections.keys()):
            if account_id not in wanted:
                await self.del_bot(account_id)
        for account_id, cfg in wanted.items():
            await self.add_bot(
                "", None, bool(cfg.get("auto_connect", False)),
                account_id=account_id,
                name=str(cfg.get("name") or account_id),
                transport=str(cfg.get("transport") or "ws"),
                app_id=str(cfg.get("app_id") or ""),
                app_secret=str(cfg.get("app_secret") or ""),
            )

    async def add_bot(
        self,
        ws_url: str,
        owner_id: int | None,
        auto_connect: bool = False,
        account_id: str | None = None,
        conn_mode: str = "forward",
        name: str = "",
        *,
        protocol: str = "qqbot",
        transport: str = "ws",
        app_id: str = "",
        app_secret: str = "",
    ) -> str:
        """新增/更新官方账号连接（不立即连接）。"""
        if not account_id:
            raise ValueError("add_bot 需要 account_id：账号 id 由用户在添加账号时设定")
        key = str(account_id)
        conn = self.connections.get(key)
        if conn is not None:
            changed = (
                conn.app_id != app_id
                or conn.app_secret != app_secret
                or conn.transport != transport
            )
            conn.account_name = name or conn.account_name
            conn.auto_connect = bool(auto_connect)
            conn.app_id = app_id
            conn.app_secret = app_secret
            conn.transport = transport
            conn.api.app_id = app_id
            conn.api.app_secret = app_secret
            conn.api.tokens.app_id = app_id
            conn.api.tokens.app_secret = app_secret
            # 主动消息开关是进程级配置，重载时同步到已建连接
            conn.allow_active_message = bool(getattr(self.settings, "qqbot_allow_active_message", False))
            conn.active_daily_limit = int(getattr(self.settings, "qqbot_active_daily_limit", 20) or 0)
            if changed and conn.status in ("connected", "connecting"):
                await self.disconnect_bot(key)
            return key
        conn = QqBotConnection(
            account_id=key,
            account_name=name or key,
            app_id=app_id,
            app_secret=app_secret,
            transport=transport,
            auto_connect=bool(auto_connect),
            allow_active_message=bool(getattr(self.settings, "qqbot_allow_active_message", False)),
            active_daily_limit=int(getattr(self.settings, "qqbot_active_daily_limit", 20) or 0),
            log=self.log,
        )
        conn.known_groups_provider = self._known_groups_for(key)
        conn._refresh_group_lists()
        if self.outbound_hook_factory:
            conn.outbound_hook = self.outbound_hook_factory(conn)
        conn.send_hook_registry = self.send_hook_registry
        conn.before_send_hook_registry = self.before_send_hook_registry
        conn.api_hook_registry = self.api_hook_registry
        self.connections[key] = conn
        return key

    def _known_groups_for(self, account_id: str) -> Callable[[], list[dict]]:
        """本地 known_groups 缓存读取器（官方账号的群列表来源之一）。"""
        def _provider() -> list[dict]:
            service = getattr(self, "config_service", None)
            if service is None:
                return []
            try:
                return service.list_known_groups(account_id) or []
            except Exception as e:  # noqa: BLE001 —— 读缓存失败只影响群名展示
                self.log.debug(f"[QQBot] 读取 known_groups 失败（{account_id}）: {e}")
                return []
        return _provider

    async def del_bot(self, account_id: str) -> None:
        key = str(account_id)
        await self.disconnect_bot(key)
        self.connections.pop(key, None)
        self._connect_locks.pop(key, None)

    # ==================== 连接管理 ====================
    async def connect_bot(self, account_id: str) -> bool:
        key = str(account_id)
        conn = self.connections.get(key)
        if conn is None:
            return False
        lock = self._connect_locks.setdefault(key, asyncio.Lock())
        async with lock:
            existing = self.bot_server_tasks.get(key)
            if existing is not None and not existing.done():
                conn.status = "connected"
                return True
            conn.suppress_auto_reconnect = False
            conn._closed = False
            conn.connect_epoch += 1
            conn.status = "connecting"
            conn.last_error = None
            conn._last_notified_status = None
            if str(conn.transport or "ws") == "webhook":
                # Webhook 模式没有「框架连出去」的长连接：事件由回调路由推进来，
                # 这里只准备 HTTP 会话 + 派发 worker，并把账号标在线。
                await conn.ensure_session()
                self._ensure_webhook_worker(key)
                conn.status = "connected"
                conn.reconnect_attempts = 0
                conn.login_retrieved = True
                conn.login_event.set()
                conn.login_info = conn.login_info or {
                    "user_id": conn.bot_id,
                    "nickname": conn.account_name,
                    "app_id": conn.app_id,
                }
                await self._notify_status(conn, "connected", "Webhook 模式")
                if self.login_handler is not None:
                    try:
                        await self.login_handler(conn)
                    except Exception as e:  # noqa: BLE001 —— 登录装配失败只影响本账号
                        self.log.warning(f"官方账号「{bot_label(conn)}」登录装配失败: {e}")
                return True
            self.bot_server_tasks[key] = asyncio.create_task(
                self._run_account(key), name=f"qqbot_account:{key}"
            )
            await self._notify_status(conn, "connecting")
            return True

    async def disconnect_bot(self, account_id: str, *, manual: bool = False) -> None:
        key = str(account_id)
        conn = self.connections.get(key)
        if conn is not None:
            conn._disconnecting = True
            conn._closed = True
            conn.connect_epoch += 1
            if manual:
                conn.suppress_auto_reconnect = True
        task = self.bot_server_tasks.pop(key, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:  # noqa: BLE001 —— 读循环收尾异常只留痕
                self.log.debug(f"[QQBot] 读循环收尾异常（{key}）: {e}")
        await self._stop_webhook_worker(key)
        if conn is not None:
            try:
                await conn.close()
            except Exception as e:  # noqa: BLE001
                self.log.debug(f"[QQBot] 关闭连接失败（{key}）: {e}")
            conn.status = "disconnected"
            await self._notify_status(conn, "disconnected")
            conn._disconnecting = False

    async def reconnect_bot(self, account_id: str) -> bool:
        await self.disconnect_bot(account_id)
        conn = self.connections.get(str(account_id))
        if conn is None:
            return False
        return await self.connect_bot(account_id)

    async def shutdown(self) -> None:
        for account_id in list(self.connections.keys()):
            await self.disconnect_bot(account_id)
        task = self._supervise_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:  # noqa: BLE001 —— 监督循环收尾异常只留痕
                self.log.debug(f"[QQBot] 监督循环收尾异常: {e}")
        self._supervise_task = None

    # ==================== Webhook 事件入口 ====================
    def _ensure_webhook_worker(self, account_id: str) -> None:
        """Webhook 模式的事件队列/worker（幂等）。"""
        key = str(account_id)
        task = self._webhook_workers.get(key)
        if task is not None and not task.done():
            return
        queue = self._webhook_queues.get(key)
        if queue is None:
            queue = asyncio.Queue(maxsize=EVENT_QUEUE_MAX)
            self._webhook_queues[key] = queue
        self._webhook_workers[key] = asyncio.create_task(
            self._dispatch_worker(key, queue), name=f"qqbot_webhook_dispatch:{key}"
        )

    async def _stop_webhook_worker(self, account_id: str) -> None:
        key = str(account_id)
        task = self._webhook_workers.pop(key, None)
        self._webhook_queues.pop(key, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:  # noqa: BLE001 —— worker 收尾异常只留痕
                self.log.debug(f"[QQBot] Webhook worker 收尾异常（{key}）: {e}")

    async def handle_webhook(self, account_id: str, payload: dict) -> bool:
        """回调路由验签后调用：把一条 op=0 事件送进该账号的处理队列。"""
        key = str(account_id)
        conn = self.connections.get(key)
        if conn is None:
            return False
        self._ensure_webhook_worker(key)
        queue = self._webhook_queues.get(key)
        if queue is None:
            return False
        item = {
            "t": str((payload or {}).get("t") or ""),
            "d": (payload or {}).get("d") or {},
            "id": (payload or {}).get("id") or "",
            "s": (payload or {}).get("s"),
        }
        await self._handle_payload(conn, item, queue)
        return True

    # ==================== 读循环 ====================
    async def _run_account(self, account_id: str) -> None:
        conn = self.connections.get(account_id)
        if conn is None:
            return
        queue: asyncio.Queue = asyncio.Queue(maxsize=EVENT_QUEUE_MAX)
        worker = asyncio.create_task(self._dispatch_worker(account_id, queue), name=f"qqbot_dispatch:{account_id}")
        backoff = 1.0
        try:
            while not conn._closed:
                try:
                    await conn.ensure_session()
                    await conn.open()
                    await self._notify_status(conn, "connecting")
                    async for item in await conn.events():
                        if conn._closed:
                            break
                        await self._handle_payload(conn, item, queue)
                    # 生成器正常结束 = 连接关闭
                    raise QqBotReconnect("事件流结束", resume=True)
                except asyncio.CancelledError:
                    raise
                except QqBotReconnect as e:
                    backoff = 1.0 if not e.resume else max(1.0, backoff)
                    conn.last_error = e.reason
                    conn.reconnect_attempts += 1
                    self.log.info(f"官方账号「{bot_label(conn)}」连接中断：{e.reason}（将重连）")
                    await self._notify_status(conn, "reconnecting", e.reason)
                except Exception as e:  # noqa: BLE001 —— 任何异常都走重连，不炸进程
                    conn.last_error = str(e)
                    conn.reconnect_attempts += 1
                    self.log.error(f"官方账号「{bot_label(conn)}」连接异常：{e}")
                    await self._notify_status(conn, "error", str(e))
                finally:
                    try:
                        await conn.close()
                    except Exception as e:  # noqa: BLE001 —— 关闭失败不阻断重连
                        self.log.debug(f"[QQBot] 关闭连接失败（{account_id}）: {e}")
                    conn.login_retrieved = False
                    conn.login_event.clear()
                    conn.status = "disconnected" if not conn._closed else conn.status
                if conn._closed or conn.suppress_auto_reconnect:
                    break
                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    raise
                backoff = min(backoff * 2, MAX_BACKOFF)
        finally:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
            except Exception as e:  # noqa: BLE001 —— 派发 worker 收尾异常只留痕
                self.log.debug(f"[QQBot] 派发 worker 收尾异常（{account_id}）: {e}")
            if self.bot_server_tasks.get(account_id) is asyncio.current_task():
                self.bot_server_tasks.pop(account_id, None)

    async def _handle_payload(self, conn: QqBotConnection, item: dict, queue: asyncio.Queue) -> None:
        name = str(item.get("t") or "")
        data = item.get("d") or {}
        self.event_history.record(conn.account_id, {"event_type": name, "raw": data, **data})
        if name == "READY":
            conn.handle_ready(data)
            conn.login_info = dict(conn.login_info)
            conn.status = "connected"
            conn.reconnect_attempts = 0
            await self._record_login(conn)
            await self._notify_status(conn, "connected", "已登录")
            if self.login_handler is not None:
                try:
                    await self.login_handler(conn)
                except Exception as e:  # noqa: BLE001 —— 登录装配失败只影响本账号
                    self.log.warning(f"官方账号「{bot_label(conn)}」登录装配失败: {e}")
            return
        if name == "GROUP_ADD_ROBOT":
            group_id = group_of(data)
            if group_id:
                conn.learn_group(group_id)
                await self._remember_group(conn, group_id)
            return
        if name == "GROUP_DEL_ROBOT":
            group_id = group_of(data)
            if group_id:
                conn.forget_group(group_id)
            return

        key = dedupe_key(name, data, item.get("id"))
        if key and conn.inbox.is_duplicate(f"{conn.account_id}:{key}"):
            self.log.debug(f"[QQBot] 丢弃重复事件（{conn.account_id} {key}）")
            return
        scene, target = message_target(data)
        if scene == "group" and target:
            conn.learn_group(target)
            await self._remember_group(conn, target)
        event = decode_event(name, data, conn)
        if event is None:
            return
        if scene and target:
            conn.inbox.remember_inbound(scene, target, data.get("id") or item.get("id"))
        self._log_received(conn, event)
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            self.event_queue_drops[conn.account_id] = self.event_queue_drops.get(conn.account_id, 0) + 1
            try:
                queue.get_nowait()  # 丢最旧
                queue.put_nowait(event)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    def _log_received(self, conn: QqBotConnection, event: Any) -> None:
        """把收到的消息/通知写进接收日志（与 OneBot 同一套渲染口径）。

        OneBot 网关在 ``_process_event`` 里显式调用 ``received_log_lines``；官方网关
        之前只把事件塞进诊断缓冲，导致 ``debug.log`` 里完全看不到“收到消息”。
        这里补上同样的 ``[接收<-][账号][群名(群 id)] 昵称(id): 内容`` 行。
        """
        if not event:
            return
        try:
            recv = getattr(conn, "_recv_logger", None)
            if recv is None:
                recv = self.log.prefix(f"Recv][{bot_label(conn)}")
                conn._recv_logger = recv
            lines = received_log_lines(
                conn, event, lambda gid: conn.learned_groups.get(str(gid), "")
            )
            for prefix, text in lines:
                recv.prefix(prefix).info(text)
        except Exception as e:  # noqa: BLE001 —— 日志失败绝不能影响收消息
            self.log.debug(f"[QQBot] 接收日志渲染失败（{conn.account_id}）: {e}")

    async def _dispatch_worker(self, account_id: str, queue: asyncio.Queue) -> None:
        conn = self.connections.get(account_id)
        while True:
            event = await queue.get()
            if self.dispatch_handler is None:
                continue
            try:
                await self.dispatch_handler(event)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 —— 单条事件失败不影响后续
                self.log.exception(f"官方账号「{bot_label(conn) if conn else account_id}」事件处理异常: {e}")

    async def _remember_group(self, conn: QqBotConnection, group_id: str) -> None:
        """把学到的群写进本地 known_groups 缓存，并尽量补全群名。

        官方没有「机器人已加入群列表」接口，但**有** `GET /v2/groups/{openid}/info`
        （部分账号有权限，真机 2026-11-11 验证可用）。群名按群只查一次：
        - 成功 → 写入 known_groups.name；
        - 失败（无权限/限频）→ 记入 _group_name_denied，后续消息不再重复打接口。
        """
        service = getattr(self, "config_service", None)
        if service is None:
            return
        name = conn.learned_groups.get(group_id) or ""
        if name == group_id:
            name = ""  # 历史缓存可能把 group_id 当名字存，不算“已知群名”
        deny_key = (conn.account_id, group_id)
        if not name and deny_key not in self._group_name_denied:
            resp = await conn.get_group_info(group_id)
            if resp.get("status") == "ok":
                name = str((resp.get("data") or {}).get("group_name") or "")
            else:
                self._group_name_denied.add(deny_key)
                self.log.debug(
                    f"[QQBot] 群信息接口不可用（{conn.account_id}/{group_id}）: "
                    f"{(resp or {}).get('message') or (resp or {}).get('retcode')}"
                )
        try:
            await service.upsert_known_groups(conn.account_id, [{
                "group_id": group_id,
                "group_name": name,
                "in_group": True,
            }])
        except Exception as e:  # noqa: BLE001 —— 群缓存失败不影响收消息
            self.log.debug(f"[QQBot] 写入 known_groups 失败（{conn.account_id}/{group_id}）: {e}")

    async def _record_login(self, conn: QqBotConnection) -> None:
        if self.account_saver is None:
            return
        try:
            await self.account_saver(
                conn.account_id, conn.bot_id, str(conn.login_info.get("nickname") or "")
            )
        except Exception as e:  # noqa: BLE001
            self.log.warning(f"[QQBot] 记录登录账号失败（{bot_label(conn)}）: {e}")

    # ==================== 监督循环 ====================
    def _ensure_supervise_task(self) -> None:
        task = self._supervise_task
        if task is None or task.done():
            self._supervise_task = asyncio.create_task(self._supervise(), name="qqbot_supervise")

    async def _supervise(self) -> None:
        while True:
            await asyncio.sleep(SUPERVISE_INTERVAL)
            try:
                await self.reconcile()
                for account_id, conn in list(self.connections.items()):
                    if str(conn.transport or "ws") == "webhook":
                        continue  # Webhook 没有长连接可重连（回调地址常驻）
                    task = self.bot_server_tasks.get(account_id)
                    if conn.auto_connect and not conn.suppress_auto_reconnect and (task is None or task.done()):
                        self.log.info(f"官方账号「{bot_label(conn)}」掉线，自动重连")
                        await self.connect_bot(account_id)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 —— 监督循环不能因单轮异常退出
                self.log.warning(f"[QQBot] 监督循环异常（已忽略本轮回合）: {e}")

    # ==================== 状态广播 ====================
    async def _notify_status(self, conn: QqBotConnection, state: str, detail: str = "") -> None:
        marker = (state, detail)
        if getattr(conn, "_last_notified_status", None) == marker:
            return
        conn._last_notified_status = marker
        try:
            await event_bus.publish(BotLifecycleEvent(
                bot_id=conn.bot_id or None,
                state=state,
                detail=detail,
                account_id=conn.account_id,
                account_name=conn.account_name,
            ))
        except Exception as e:  # noqa: BLE001
            self.log.warning(f"[QQBot] 状态广播失败: {e}")
        if self.lifecycle_hook_registry is not None:
            try:
                await self.lifecycle_hook_registry.run(conn, state, detail)
            except Exception as e:  # noqa: BLE001
                self.log.warning(f"[QQBot] 生命周期钩子失败: {e}")
        if self.status_handler is not None:
            try:
                await self.status_handler(conn, state, detail)
            except Exception as e:  # noqa: BLE001
                self.log.warning(f"[QQBot] 状态回调失败: {e}")

    # ==================== 对外查询 ====================
    def get_bot(self, account_id: str) -> QqBotConnection | None:
        return self.connections.get(str(account_id))

    def find_conn_by_bot_id(self, bot_id: Any) -> QqBotConnection | None:
        for conn in self.connections.values():
            if conn.bot_id is not None and str(conn.bot_id) == str(bot_id):
                return conn
        return None

    def get_last_account(self, account_id: str) -> dict | None:
        if not self.account_lookup:
            return None
        try:
            return (self.account_lookup() or {}).get(str(account_id))
        except Exception as e:  # noqa: BLE001
            self.log.debug(f"[QQBot] 读取上次登录快照失败: {e}")
            return None

    def get_bots_info(self) -> list[dict]:
        from app.infrastructure.onebot.introspect import bot_info

        return [
            bot_info(conn, last_account=self.get_last_account(account_id), listening=False)
            for account_id, conn in self.connections.items()
        ]

    def get_bot_info(self, account_id: str) -> dict | None:
        conn = self.connections.get(str(account_id))
        if conn is None:
            return None
        from app.infrastructure.onebot.introspect import bot_info

        return bot_info(conn, last_account=self.get_last_account(str(account_id)), listening=False)

    def recent_events(self, account_id: str, limit: int = 50) -> list[dict]:
        return self.event_history.recent(account_id, limit)

    def clear_events(self, account_id: str) -> None:
        self.event_history.clear(account_id)

    def latest_group_id(self, account_id: str) -> int | str | None:
        return self.event_history.latest_group_id(account_id)

    # ==================== 测试辅助 ====================
    @property
    def reverse_servers(self) -> dict:
        return {}
