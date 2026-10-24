"""OneBot WebSocket 网关。

替代原 webserver/ws_connect.py + ws_botserver.py：
- 管理多账号连接 / 重连 / 断开；
- 消息循环：API 响应消费 → payload 解码 → 转发消息拉取 → 日志 → 派发；
- 登录后通过注入的 login_handler 触发模块加载（解耦 infrastructure 与 modules）。

## 两种连接方向

账号配置里的 ``conn_mode`` 决定本网关对该账号做什么：

- ``forward``（正向 WS）：**框架连出去**——``connect_bot`` 打开到 ``ws_url`` 的客户端连接；
- ``reverse``（反向 WS）：**框架监听**——``connect_bot`` 在该地址起监听，等 OneBot
  实现连入（见 ``reverse.py``）。接入后走同一套 ``_bot_server`` 消息循环。

两种模式的差别只在「谁发起 TCP 连接」，账号状态、登录快照、监督重连、API 收发完全共用。

## 身份：account_id 与 name

- ``account_id``：用户在「添加账号」时设定的唯一 id，创建后不可变。连接表、任务表、
  连接锁**全部以它为 key**。历史实现用数组下标 ``index`` 当 key，删中间账号会整体
  前移、身份漂移，已废弃。
- ``account_name``：展示名。日志一律打名字（``账号「主号」``），不打 id。
- ``conn.bot_id``：本次登录的 QQ 号，纯运行时信息，登录后才有。

账号隔离：每个账号独立处理自己收到的每条消息，不做跨账号"同群去重"。
LLM 运行时按 ``event.account_id`` 取用，触发规则（@ / 关键词）由各账号自行判定；
确需"同群只由一个账号响应"时用 WebUI 的单一服务（single_service）显式声明。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.event_bus import BotLifecycleEvent, event_bus
from app.core.logger import logger
from app.domain.events import BaseEvent, MessageEvent
from app.infrastructure.cache import Cache
from app.infrastructure.config.config_service import mask_ws_url
from app.infrastructure.onebot.client import BotConnection
from app.infrastructure.onebot.codec import decode
from app.infrastructure.onebot.endpoint import FORWARD, is_reverse, normalize_mode
from app.infrastructure.onebot.forward import connect_forward
from app.infrastructure.onebot.introspect import bot_info, bots_info, conn_mode, effective_bot_id
from app.infrastructure.onebot.log_text import (  # noqa: F401 - 原路径重导出，第三方插件仍在 import
    _event_text,
    _message_log_text,
    _raw_payload_text,
    received_log_lines,
)
from app.infrastructure.onebot.reverse_link import ReverseLink

LoginHandler = Callable[[BotConnection], Awaitable[None]]
# 登录成功后记录账号快照（account_id, user_id, nickname）→ 返回是否发生变化
AccountSaver = Callable[[str, Any, str], Awaitable[bool]]


class OneBotGateway:
    def __init__(
        self,
        *,
        settings,
        cache: Cache,
        logger_: Any = None,
        login_handler: LoginHandler | None = None,
    ) -> None:
        self.settings = settings
        self.cache = cache
        self.log = logger_ or logger
        self.login_handler = login_handler

        # 全部以 account_id 为 key（账号 id 不可变，删账号不会让别人的 key 漂移）
        self.connections: dict[str, BotConnection] = {}
        self.bot_server_tasks: dict[str, asyncio.Task] = {}
        # 反向 WS 监听器管理（正向账号不进这里）。对外仍暴露 reverse_servers 只读视图。
        self.reverse_link = ReverseLink(self)
        self.connect_type = False
        self._supervise_task: asyncio.Task | None = None
        self._connect_locks: dict[str, asyncio.Lock] = {}  # 防止监督循环与 WebUI 并发双开
        # 登录成功时的账号快照写入器（BotService 注入 → ConfigService）；
        # 供前端在断开状态回退展示「上次连接的账号」。
        self.account_saver: AccountSaver | None = None
        # 账号快照读取器（BotService 注入 → ConfigService.get_bot_accounts）
        self.account_lookup: Callable[[], dict[str, dict]] | None = None
        # 出站拦截钩子工厂（bootstrap 注入）：传入连接 → 返回 (action, params) 钩子
        self.outbound_hook_factory = None
        # 插件钩子注册表（bootstrap 注入）
        self.send_hook_registry = None
        self.before_send_hook_registry = None
        self.api_hook_registry = None
        self.lifecycle_hook_registry = None

    # ==================== 对外查询 ====================
    def get_last_account(self, account_id: str) -> dict | None:
        """上一次成功登录的账号快照（断开后前端回退展示用）。"""
        if not self.account_lookup:
            return None
        return self.account_lookup().get(str(account_id))

    def get_bots_info(self) -> list[dict]:
        """账号对外视图，顺序跟随配置（界面顺序），而不是连接字典的插入顺序。"""
        provider = getattr(self, "bots_provider", None)
        if provider:
            ordered = [
                self.connections[cfg["id"]]
                for cfg in provider()
                if cfg.get("id") in self.connections
            ]
        else:
            ordered = list(self.connections.values())
        return bots_info(ordered, self.account_lookup, self.reverse_link.listening)

    @staticmethod
    def _conn_mode(conn) -> str:
        """连接方向（缺字段的老连接对象按正向处理）。"""
        return conn_mode(conn)

    def _reverse_listening(self, account_id: str | None) -> bool:
        """该账号的反向监听器是否正在监听（正向账号恒为 False）。"""
        return self.reverse_link.listening(account_id)

    @property
    def reverse_servers(self) -> dict:
        """反向监听器表（排查/测试用；生命周期由 ReverseLink 负责）。"""
        return self.reverse_link.servers

    @staticmethod
    def _effective_bot_id(conn, last_account: dict | None) -> int | None:
        """对外公布的 QQ 号（详见 ``introspect.effective_bot_id``）。"""
        return effective_bot_id(conn, last_account)

    def get_bot_info(self, account_id: str) -> dict | None:
        conn = self.connections.get(str(account_id))
        if not conn:
            return None
        return bot_info(
            conn,
            last_account=self.get_last_account(conn.account_id),
            listening=self.reverse_link.listening(conn.account_id),
        )

    async def get_bot_id(self, account_id: str) -> int | None:
        """等该账号登录完成并返回它的 QQ 号（运行时信息，不是身份）。"""
        conn = self.connections.get(str(account_id))
        if not conn:
            return None
        for _ in range(100):  # 最多等待 10s
            if conn.bot_id:
                return conn.bot_id
            await asyncio.sleep(0.1)
        return None

    def get_bot(self, account_id: str) -> BotConnection | None:
        return self.connections.get(str(account_id))

    def find_conn_by_bot_id(self, bot_id: int) -> BotConnection | None:
        for conn in self.connections.values():
            if conn.bot_id == bot_id:
                return conn
        return None

    # ==================== 状态广播 ====================
    async def _notify_status(self, conn, state: str, detail: str = "") -> None:
        """广播账号连接状态变化（WebUI 实时刷新；同一状态不重复广播）。

        走框架 EventBus（app/core/event_bus.py），由 WebUI 订阅后经 WS 推送前端；
        无订阅者时零开销（测试环境安全）。
        """
        if getattr(conn, "_last_notified_status", None) == state:
            return
        conn._last_notified_status = state
        try:
            await event_bus.publish(BotLifecycleEvent(
                bot_id=conn.bot_id or None,
                state=state,
                detail=detail,
                account_id=conn.account_id,
                account_name=conn.account_name,
            ))
        except Exception as e:
            self.log.warning(f"[Gateway] 状态广播失败: {e}")

        # 插件级 Bot 生命周期钩子（@bot_lifecycle_hook）
        if self.lifecycle_hook_registry is not None:
            try:
                await self.lifecycle_hook_registry.run(conn, state, detail)
            except Exception as e:
                self.log.warning(f"[Gateway] 生命周期钩子执行失败: {e}")

    # ==================== 连接管理 ====================
    async def add_bot(
        self,
        ws_url: str,
        owner_id: int | None,
        auto_connect: bool = False,
        account_id: str | None = None,
        conn_mode: str = FORWARD,
        name: str = "",
    ) -> str:
        """新增（或按显式 account_id 更新）一个账号连接。

        ``owner_id`` 参数**仅为兼容旧调用方保留**：v3 起账号级 owner QQ 已废弃
        （拥有者身份只来自「权限管理 → 拥有者名册」），传进来的值会被忽略。

        ``conn_mode``：``forward`` = 正向 WS（连出去）/ ``reverse`` = 反向 WS（本地监听）。
        """
        if not account_id:
            raise ValueError("add_bot 需要 account_id：账号 id 由用户在添加账号时设定")
        key = str(account_id)
        mode = normalize_mode(conn_mode)
        conn = self.connections.get(key)
        if conn is not None:  # 已存在 → 仅更新配置字段，不重建连接
            conn.ws_url = ws_url
            conn.auto_connect = auto_connect
            conn.conn_mode = mode
            if name:
                conn.account_name = name
            return key
        conn = BotConnection(ws_url=ws_url, auto_connect=auto_connect, conn_mode=mode)
        conn.status = "disconnected"
        conn.account_id = key
        conn.account_name = name or key
        if self.outbound_hook_factory:
            conn.outbound_hook = self.outbound_hook_factory(conn)
        conn.send_hook_registry = self.send_hook_registry
        conn.before_send_hook_registry = self.before_send_hook_registry
        conn.api_hook_registry = self.api_hook_registry
        self.connections[key] = conn
        return key

    async def readd_bot(
        self, ws_url: str, owner_id: int | None, account_id: str, conn_mode: str | None = None
    ) -> str:
        """更新已有连接的地址（``owner_id`` 同上：兼容保留，已忽略）。"""
        key = str(account_id)
        conn = self.connections.get(key)
        if conn:
            conn.ws_url = ws_url
            if conn_mode is not None:
                conn.conn_mode = normalize_mode(conn_mode)
        return key

    async def del_bot(self, account_id: str) -> None:
        key = str(account_id)
        await self.disconnect_bot(key)
        self.connections.pop(key, None)
        self._connect_locks.pop(key, None)  # 清理连接锁，防增删账号后泄漏

    async def connect_bot(self, account_id: str) -> bool:
        key = str(account_id)
        conn = self.connections.get(key)
        if not conn:
            return False
        lock = self._connect_locks.setdefault(key, asyncio.Lock())
        async with lock:  # 防止监督循环与 WebUI 并发双开连接
            if key in self.bot_server_tasks:
                conn.status = "connected"
                self.log.info(f"账号「{conn.label}」已有连接")
                return True

            conn.status = "connecting"
            conn.last_error = None
            # 走到这里 = 显式连接请求（WebUI 连接/重连，或监督循环的掉线恢复）：
            # 解除「用户手动断开」的抑制，让该账号重新参与自动重连。
            conn.suppress_auto_reconnect = False
            if is_reverse(self._conn_mode(conn)):
                # 反向：起监听等 OneBot 实现连入（成败不依赖对端是否已在线）
                return await self._listen_reverse(key, conn)

            epoch = getattr(conn, "connect_epoch", 0)
            websocket = await self._connect_websocket(conn.ws_url)
            if getattr(conn, "connect_epoch", 0) != epoch:
                # 握手期间收到了断开请求 → 本次连接已作废，丢弃结果（不要覆盖 disconnected 状态）
                if websocket is not None:
                    with contextlib.suppress(Exception):
                        await websocket.close()
                self.log.info(f"账号「{conn.label}」连接已取消（握手期间收到断开请求）")
                return False
            if websocket:
                conn.websocket = websocket
                conn.status = "connected"
                conn.reconnect_attempts = 0
                self.log.info(f"账号「{conn.label}」连接成功: {mask_ws_url(conn.ws_url)}")
                self.bot_server_tasks[key] = asyncio.create_task(
                    self._bot_server(key), name=f"bot_server:{key}"
                )
                await self._notify_status(conn, "connected")
                return True
            conn.reconnect_attempts += 1
            conn.status = "error"
            conn.last_error = f"连接失败: {mask_ws_url(conn.ws_url)}"
            conn._last_connect_attempt = time.time()  # 失败也记账，保证监督循环退避生效
            self.log.error(f"账号「{conn.label}」连接失败: {mask_ws_url(conn.ws_url)}")
            await self._notify_status(conn, "error", conn.last_error)
            return False

    # ==================== 反向 WS（框架监听） ====================
    # 监听器、接入、等待接入的状态收敛都在 ReverseLink 里（见 reverse_link.py）；
    # 这里只留模式分支，让「正向连出」这条主线仍然一眼读完。
    async def _listen_reverse(self, account_id: str, conn) -> bool:
        return await self.reverse_link.ensure_listening(account_id, conn)

    async def disconnect_bot(self, account_id: str, *, manual: bool = False) -> None:
        """断开连接。

        ``manual=True`` = 用户在 WebUI 主动点「断开」（``/api/bots/{id}/disconnect``）：
        置位 ``suppress_auto_reconnect``，监督循环不再把这个账号自动连回来，
        直到用户显式「连接 / 重连」（或重新打开它的自动连接开关 / 重启进程）。

        不加这个标记时，``_supervise`` 会把「status=disconnected 且 auto_connect=True」
        当成掉线，最多 10s 后又连接 —— 日志里表现为「断开连接」之后紧跟「连接成功」，
        即「点了断开，一段时间后又连接」。
        """
        key = str(account_id)
        conn = self.connections.get(key)
        if conn is not None:
            if manual:
                conn.suppress_auto_reconnect = True
                self.log.info(
                    f"账号「{conn.label}」用户主动断开：本次运行期间不再自动重连（连接/重连可恢复）"
                )
            # 作废正在握手的 connect_bot（见 connect_bot 的 epoch 校验），
            # 否则握手成功会把刚置好的断开状态覆盖回 connected。
            conn.connect_epoch = getattr(conn, "connect_epoch", 0) + 1

        # 反向监听器随「断开」一并停掉：用户的断开语义包含「不再占端口等人接入」
        await self.reverse_link.stop(key)

        task = self.bot_server_tasks.pop(key, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if conn:
            if conn.websocket:
                try:
                    await conn.websocket.close()
                except Exception as e:
                    self.log.warning(f"关闭 WebSocket 异常: {e}")
                conn.websocket = None
            await self._notify_status(conn, "disconnected")  # 先广播（保留 bot_id）
            self._reset_conn_state(conn)
            conn.status = "disconnected"
            self.log.info(f"账号「{conn.label}」断开连接")

    @staticmethod
    def _reset_conn_state(conn) -> None:
        """断开后清空登录态，并让在途 API 请求立即失败（避免调用方阻塞至超时）。"""
        conn.bot_id = 0
        conn.login_info = {}
        conn.all_group_list = []
        conn.all_group_list_info = []
        fail_pending = getattr(conn, "fail_pending", None)
        if fail_pending:
            fail_pending()

    async def reconnect_bot(self, account_id: str) -> bool:
        key = str(account_id)
        await self.disconnect_bot(key)
        conn = self.connections.get(key)
        if not conn:
            return False
        conn.reconnect_attempts += 1
        return await self.connect_bot(key)

    async def _connect_websocket(self, url: str):
        """正向连接（传输细节见 ``forward.py``）。"""
        return await connect_forward(url, settings=self.settings, log=self.log)

    # ==================== 登录信息 ====================
    async def _record_login_account(self, conn: BotConnection) -> None:
        """记录本次登录账号快照（供断开后回退展示）。

        失败只告警，绝不影响登录主流程；账号未变化时 saver 内部为 no-op，
        因此重连同一账号不会反复写库/刷屏。
        """
        if not self.account_saver:
            return
        try:
            await self.account_saver(
                conn.account_id, conn.bot_id, (conn.login_info or {}).get("nickname", "")
            )
        except Exception as e:
            self.log.warning(f"[Gateway] 记录上次登录账号失败（{conn.label}）: {e}")

    async def _get_login_info(self, conn: BotConnection) -> bool:
        try:
            resp = await conn.get_login_info()
            if resp and resp.get("status") == "ok":
                data = resp.get("data", {}) or {}
                conn.bot_id = int(data.get("user_id", 0) or 0)
                conn.login_info = data
                self.log.info(f"登录成功 | {conn.label} | {conn.bot_id} | {data.get('nickname', '')}")
                await self._record_login_account(conn)
                if self.login_handler:
                    await self.login_handler(conn)
                # 强制重新广播（此时才有真实 bot_id，前端据此刷新模块数据）
                conn._last_notified_status = None
                await self._notify_status(conn, "connected", "已登录")
                self.connect_type = False
                return True
            self.log.info(f"账号「{conn.label}」获取账号信息失败，将尝试从消息流中自动获取 self_id")
            self.connect_type = False
            return False
        except Exception as e:
            self.log.error(f"获取账号信息失败: {e}")
            self.connect_type = False
            return False

    async def _get_login_groups(self, conn: BotConnection) -> bool:
        try:
            resp = await conn.get_group_list()
            if resp and resp.get("status") == "ok":
                conn.all_group_list_info = resp.get("data", []) or []
                conn.all_group_list = [
                    g.get("group_id", 0) for g in conn.all_group_list_info if g.get("group_id")
                ]
                return True
            self.log.error(f"获取群聊列表失败（{conn.label}）")
        except Exception as e:
            self.log.error(f"获取群聊列表失败: {e}")
        return False

    def _lookup_group_name(self, conn: BotConnection, group_id: int) -> str:
        """从登录时缓存的群列表信息里查群名。"""
        for g in conn.all_group_list_info or []:
            if str(g.get("group_id", "")) == str(group_id):
                return g.get("group_name", "")
        return ""

    # ==================== 消息循环 ====================
    async def _bot_server(self, account_id: str, *, login_immediately: bool = False) -> None:
        key = str(account_id)
        conn = self.connections.get(key)
        if not conn or not conn.websocket:
            return
        # 本循环只读这一条连接：反向账号可能被「新接入」替换，收尾靠 identity 判断
        ws = conn.websocket
        me = asyncio.current_task()
        bot_logger = self.log.add_info(conn.label)
        conn.bot_id = 0
        start_type = False
        login_tasks: list[asyncio.Task] = []
        if login_immediately:
            # 反向连接：OneBot 实现可能十几秒才推第一个事件，握手成功即主动拉一次
            start_type = True
            login_tasks = [
                asyncio.create_task(self._get_login_info(conn)),
                asyncio.create_task(self._get_login_groups(conn)),
            ]
            bot_logger.info("反向连接已接入，正在获取登录信息")
        # 事件处理 worker：接收循环只负责「解析 + API 响应匹配 + 入队」，
        # 耗时处理（模块调用 / 转发拉取 / LLM 触发）在 worker 串行执行。
        # 若不这样拆分，模块内 await API 响应（如 send_private_msg 等 echo）时，
        # 接收循环被阻塞、无法消费该响应 → 请求/响应自锁 → 10s 超时。
        queue: asyncio.Queue = asyncio.Queue(maxsize=1024)  # 有界背压，防异常洪峰撑爆内存
        worker = asyncio.create_task(
            self._dispatch_worker(key, queue), name=f"dispatch_worker:{key}"
        )
        # 「连接级失败响应」在循环内已自行收尾，用它避免退出后再走一遍收尾逻辑
        connection_ended = False

        try:
            async for message in ws:
                try:
                    msg_dict = json.loads(message)
                except json.JSONDecodeError:
                    bot_logger.warning(f"收到非 JSON 消息: {message}")
                    continue

                try:
                    # 1. API 响应优先消费（含 status=ok / status=failed 的失败响应）。
                    #    必须放在状态检查之前：否则一次失败发送的响应会被误判为
                    #    连接级失败而 break，导致对应 Future 永远等不到 → API 超时。
                    if conn.handle_api_response(msg_dict):
                        continue

                    if msg_dict.get("status", "ok") != "failed":
                        if not start_type:
                            start_type = True
                            login_tasks = [
                                asyncio.create_task(self._get_login_info(conn)),
                                asyncio.create_task(self._get_login_groups(conn)),
                            ]
                            bot_logger.info("正在尝试获取登录信息")
                    elif msg_dict.get("echo"):
                        # 带 echo 的 failed 是 API 失败响应（handle_api_response 已记录未匹配），
                        # 不视为断连、也不当作事件。
                        continue
                    else:
                        # 连接级失败响应（如 token 验证失败）：视为连接失败并断开，
                        # 不应继续解码成普通事件处理。
                        bot_logger.warning(f"WebSocket失败响应: {msg_dict}")
                        reason = msg_dict.get("message") or msg_dict.get("wording") or "连接失败"
                        start_type = False
                        conn.bot_id = 0
                        for t in login_tasks:
                            t.cancel()
                        conn.status = "error"
                        conn.last_error = reason
                        await self._notify_status(conn, "error", reason)
                        try:
                            await ws.close()
                        except Exception as e:
                            # 关闭失败仅记 debug：连接随后会被置空并由监督循环重建，
                            # 但留痕便于排查"连接未真正释放"类问题
                            logger.add_info(conn.label).debug(
                                f"[Gateway] 关闭异常连接失败（已忽略）: {e}"
                            )
                        if conn.websocket is ws:
                            conn.websocket = None
                            self._reset_conn_state(conn)
                        connection_ended = True
                        break

                    # 2. 解码为领域事件并入队处理（不阻塞接收循环）
                    event = decode(msg_dict, conn)
                    if isinstance(event, BaseEvent):
                        await queue.put(event)

                except Exception as e:
                    bot_logger.error(f"处理消息异常: {e}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            connection_ended = True
            await self._handle_connection_end(conn, f"{e}", bot_logger, abnormal=True, ws=ws)
        else:
            if not connection_ended:
                # 关键：websockets 的 Connection.__aiter__ 会把「正常关闭（1000）」的
                # ConnectionClosedOK 直接吞掉、让 async for 正常退出（不抛异常）。
                # 不在这里显式收尾就会留下僵尸连接：status 永远停在 connected →
                # 监督循环判定「在线」永不重连，之后每次发送都抛 ConnectionClosed
                # 立即失败。真实案例：远端只重启约 7 秒，客户端僵死 6 小时以上，
                # 而且日志里连一条断线记录都没有。
                await self._handle_connection_end(
                    conn, self._close_reason(ws), bot_logger, abnormal=False, ws=ws
                )
        finally:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
            for t in login_tasks:
                if not t.done():
                    t.cancel()
            # 只清理自己的登记项：被新连接替换后，这一格已经是新任务
            if self.bot_server_tasks.get(key) is me:
                self.bot_server_tasks.pop(key, None)

    async def _handle_connection_end(
        self, conn, reason: str, bot_logger, *, abnormal: bool, ws: Any = None
    ) -> None:
        """连接结束的统一收尾：留痕 → 置掉线态 → 广播 → 清 ws → 在途请求立即失败。

        异常断线（ConnectionClosedError 等）与正常关闭（1000）**必须**走同一套收尾：
        前者由 ``except`` 分支触发，后者由 ``async for`` 正常退出的 ``else`` 分支触发。
        少了后者就是「僵尸连接」——状态仍 connected，监督循环永远不重连。

        ``ws`` 是**本次**结束的连接：反向模式下同一账号可能已被新连接接管，
        此时这条旧连接的收尾不得再改状态、更不得关闭新连接（否则新连接刚接上就被掐死）。
        """
        if ws is not None and conn.websocket is not None and conn.websocket is not ws:
            bot_logger.info("旧连接已结束（当前已有新连接接管），跳过状态收敛")
            return
        text = f"WebSocket 连接结束: {reason}"
        if abnormal:
            bot_logger.error(text)
        else:
            # 正常关闭是服务端主动行为（重启/下线）：必须留痕（否则无从发现），
            # 但用 WARNING，不必惊动 errors.log
            bot_logger.warning(text)
        await self._mark_offline(conn, reason)
        target = conn.websocket if ws is None else ws
        if target is not None:
            try:
                await target.close()
            except Exception as e:
                # 关闭失败仅记 debug：连接随后会被置空并由监督循环重建，但留痕便于排查
                bot_logger.debug(f"[Gateway] 关闭已结束的连接失败（已忽略）: {e}")
            if conn.websocket is target:
                conn.websocket = None
        # 清空登录态 + 让在途 API 请求立即失败（与 disconnect_bot 一致）
        self._reset_conn_state(conn)

    async def _mark_offline(self, conn, reason: str) -> None:
        """把账号置为「掉线」态并广播。

        反向模式且监听器仍在监听时状态是 ``connecting``（等 OneBot 实现重新接入）：
        对端重启属于常态，显示成红色错误会让人以为配置坏了；``last_error`` 仍保留
        原因，排查信息不丢。其余情况（正向掉线 / 反向监听已停）按 ``error`` 处理。
        """
        if is_reverse(self._conn_mode(conn)) and await self.reverse_link.mark_waiting(conn, reason):
            return
        conn.status = "error"
        conn.last_error = reason
        await self._notify_status(conn, "error", reason)

    @staticmethod
    def _close_reason(ws) -> str:
        """正常关闭的可读原因：带上 close_code/close_reason，便于定位是哪一端关的。"""
        code = getattr(ws, "close_code", None)
        reason = getattr(ws, "close_reason", None)
        if code is None:
            return "连接已结束（未收到关闭帧）"
        suffix = f" {reason}" if reason else ""
        return f"服务端关闭连接 (code={code}{suffix})"

    async def _dispatch_worker(self, account_id: str, queue: asyncio.Queue) -> None:
        """串行处理该账号的事件队列，独立于接收循环。

        单账号单 worker，保持与原「接收循环内串行派发」相同的处理顺序；
        接收循环因此始终空闲，可及时消费 API 响应。
        """
        key = str(account_id)
        conn = self.connections.get(key)
        if conn is None:
            return
        bot_logger = self.log.add_info(conn.label)
        recv_logger = self.log.prefix(f"Recv][{conn.label}")
        while True:
            event = await queue.get()
            try:
                await self._process_event(event, conn, bot_logger, recv_logger)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                bot_logger.exception(f"事件处理异常: {e}")

    async def _process_event(
        self, event: BaseEvent, conn: BotConnection, bot_logger, recv_logger
    ) -> None:
        """单条事件处理（原接收循环第 3~6 步）：转发拉取 / 日志 / 忽略 / 派发。

        不做跨账号去重：每个账号独立处理自己收到的消息（多账号同群时各自按
        自己的触发规则决定是否回复），避免"消息被其它账号吞掉后无人响应"。
        """
        # 转发消息拉取
        if isinstance(event, MessageEvent) and event.event_type in ("message_group", "message_private"):
            event = await self._attach_forward_msg(event, conn)
        # 日志：[接收<-][账号名][群名] 名字:内容（渲染口径见 log_text.received_log_lines）
        for prefix, text in received_log_lines(
            conn, event, lambda gid: self._lookup_group_name(conn, gid)
        ):
            recv_logger.prefix(prefix).info(text)

        # 忽略标记检查
        if self._is_ignore(event):
            bot_logger.debug(
                f"[忽略] 命中忽略标记，丢弃事件 {event.event_type}"
                f"（用户 {event.user_id}，账号 {conn.label}/{conn.bot_id}）"
            )
            return

        # 派发（不再为每条群消息补一条「已进入节点链」DEBUG：接收日志 + Agent 门控日志已够定位）
        await self._dispatch(event)

    async def _dispatch(self, event: BaseEvent) -> None:
        """派发到模块事件总线（由 bootstrap 注入）。"""
        handler = getattr(self, "dispatch_handler", None)
        if handler:
            await handler(event)

    async def _attach_forward_msg(self, event: MessageEvent, conn: BotConnection) -> MessageEvent:
        for seg in event.message:
            if seg.type == "forward":
                fid = seg.data.get("id")
                if fid:
                    resp = await conn.get_forward_msg(str(fid))
                    event.forward_msg = (resp or {}).get("data", {}).get("messages", []) or []
                break
        return event

    def _is_ignore(self, event: BaseEvent) -> bool:
        key = f"{event.event_type}_{event.user_id}_ignore"
        count = self.cache.get(key)
        if count:
            self.cache.set(key, count - 1, 60)
            return True
        return False

    # ==================== 生命周期 ====================
    async def start_all(self, bots_config: list[dict]) -> None:
        """按配置收敛连接列表，并行连接 auto_connect 账号，启动监督循环。"""
        await self._reconcile(bots_config)
        auto_ids = [
            aid for aid, conn in list(self.connections.items())
            if conn.auto_connect and conn.status not in ("connected", "connecting")
        ]
        if auto_ids:
            await asyncio.gather(*(self.connect_bot(aid) for aid in auto_ids))
        if self._supervise_task is None or self._supervise_task.done():
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
                self.log.info(f"账号「{conn.label}」配置已更新: {mask_ws_url(conn.ws_url)}")
                if conn.status in ("connected", "connecting") or self._reverse_listening(account_id):
                    await self.disconnect_bot(account_id)  # url/方向变更 → 断开待重连(或待重新监听)

    async def _supervise(self) -> None:
        """定期收敛连接映射，并以指数退避重连掉线的 auto_connect 账号。"""
        while True:
            await asyncio.sleep(10)
            getter = getattr(self, "bots_provider", None)
            if getter:
                try:
                    bots_config = getter()  # 同步 provider（ConfigService.get_bots）
                    await self._reconcile(bots_config)
                    await self._sweep_zombies()
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
                        await self.connect_bot(account_id)
                except Exception as e:
                    self.log.error(f"监督循环异常: {e}")

    async def _sweep_zombies(self) -> None:
        """把「状态还写着 connected、连接其实已经没了」的账号改成掉线态，交给重连逻辑。

        正常收尾（``_handle_connection_end`` / ``disconnect_bot``）都会改状态；
        这里兜住没走到收尾的静默路径（接收任务消失、ws 已被对端关闭等）——
        否则监督循环会把它们当成「在线」，永不重连，表现为静默死亡。
        """
        for account_id, conn in list(self.connections.items()):
            if conn.status != "connected" or not self._connection_gone(account_id, conn):
                continue
            self.log.warning(f"账号「{conn.label}」连接已不存在但状态仍是 connected，按掉线处理")
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

    async def shutdown(self) -> None:
        if self._supervise_task:
            self._supervise_task.cancel()
        for account_id in list(self.connections.keys()):
            await self.disconnect_bot(account_id)
