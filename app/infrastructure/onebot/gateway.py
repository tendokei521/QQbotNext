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
from app.infrastructure.onebot.codec import decode, event_name
from app.infrastructure.onebot.endpoint import FORWARD, is_reverse, normalize_mode
from app.infrastructure.onebot.forward import connect_forward
from app.infrastructure.onebot.introspect import bot_info, bots_info, conn_mode, effective_bot_id
from app.infrastructure.onebot.log_text import _event_text, _message_log_text, _raw_payload_text  # noqa: F401 - 重导出保持原路径可用
from app.infrastructure.onebot.reverse_link import ReverseLink

LoginHandler = Callable[[BotConnection], Awaitable[None]]
# 登录成功后记录账号快照（index, user_id, nickname）→ 返回是否发生变化
AccountSaver = Callable[[int, Any, str], Awaitable[bool]]


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

        self.connections: dict[int, BotConnection] = {}
        self.bot_server_tasks: dict[int, asyncio.Task] = {}
        # 反向 WS 监听器管理（正向账号不进这里）。对外仍暴露 reverse_servers 只读视图。
        self.reverse_link = ReverseLink(self)
        self.connect_type = False
        self._supervise_task: asyncio.Task | None = None
        self._connect_locks: dict[int, asyncio.Lock] = {}  # 防止监督循环与 WebUI 并发双开
        # 登录成功时的账号快照写入器（BotService 注入 → ConfigService）；
        # 供前端在断开状态回退展示「上次连接的账号」。
        self.account_saver: AccountSaver | None = None
        # 账号快照读取器（BotService 注入 → ConfigService.get_bot_accounts）
        self.account_lookup: Callable[[], dict[int, dict]] | None = None
        # 出站拦截钩子工厂（bootstrap 注入）：传入连接 → 返回 (action, params) 钩子
        self.outbound_hook_factory = None
        # 插件钩子注册表（bootstrap 注入）
        self.send_hook_registry = None
        self.before_send_hook_registry = None
        self.api_hook_registry = None
        self.lifecycle_hook_registry = None

    # ==================== 对外查询 ====================
    def get_last_account(self, index: int) -> dict | None:
        """上一次成功登录的账号快照（断开后前端回退展示用）。"""
        if not self.account_lookup:
            return None
        return self.account_lookup().get(index)

    def get_bots_info(self) -> list[dict]:
        return bots_info(
            self.connections.values(), self.account_lookup, self.reverse_link.listening
        )

    @staticmethod
    def _conn_mode(conn) -> str:
        """连接方向（缺字段的老连接对象按正向处理）。"""
        return conn_mode(conn)

    def _reverse_listening(self, index: int | None) -> bool:
        """该索引的反向监听器是否正在监听（正向账号恒为 False）。"""
        return self.reverse_link.listening(index)

    @property
    def reverse_servers(self) -> dict:
        """反向监听器表（排查/测试用；生命周期由 ReverseLink 负责）。"""
        return self.reverse_link.servers

    @staticmethod
    def _effective_bot_id(conn, last_account: dict | None) -> int | None:
        """对外公布的账号（详见 ``introspect.effective_bot_id``）。"""
        return effective_bot_id(conn, last_account)

    def get_bot_info_by_index(self, index: int) -> dict | None:
        conn = self.connections.get(index)
        if not conn:
            return None
        return bot_info(
            conn,
            last_account=self.get_last_account(conn.index),
            listening=self.reverse_link.listening(index),
        )

    async def get_bot_id(self, index: int) -> int | None:
        conn = self.connections.get(index)
        if not conn:
            return None
        for _ in range(100):  # 最多等待 10s
            if conn.bot_id:
                return conn.bot_id
            await asyncio.sleep(0.1)
        return None

    def get_bot(self, index: int) -> BotConnection | None:
        return self.connections.get(index)

    def find_conn_by_bot_id(self, bot_id: int) -> BotConnection | None:
        for conn in self.connections.values():
            if conn.bot_id == bot_id:
                return conn
        return None

    # ==================== 状态广播 ====================
    async def _notify_status(self, conn, state: str, detail: str = "") -> None:
        """广播 Bot 连接状态变化（WebUI 实时刷新；同一状态不重复广播）。

        走框架 EventBus（app/core/event_bus.py），由 WebUI 订阅后经 WS 推送前端；
        无订阅者时零开销（测试环境安全）。
        """
        if getattr(conn, "_last_notified_status", None) == state:
            return
        conn._last_notified_status = state
        try:
            await event_bus.publish(BotLifecycleEvent(
                bot_id=conn.bot_id or None,
                bot_index=conn.index,
                state=state,
                detail=detail,
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
        index: int | None = None,
        conn_mode: str = FORWARD,
    ) -> int:
        """新增（或按显式 index 更新）一个 Bot 连接。index 缺省取最大索引 + 1（避免删除中间账号后撞已有连接）。

        ``owner_id`` 参数**仅为兼容旧调用方保留**：v3 起账号级 owner QQ 已废弃
        （拥有者身份只来自「权限管理 → 拥有者名册」），传进来的值会被忽略。

        ``conn_mode``：``forward`` = 正向 WS（连出去）/ ``reverse`` = 反向 WS（本地监听）。
        """
        if index is None:
            index = max(self.connections, default=-1) + 1
        mode = normalize_mode(conn_mode)
        conn = self.connections.get(index)
        if conn is not None:  # 已存在 → 仅更新配置字段，不重建连接
            conn.ws_url = ws_url
            conn.auto_connect = auto_connect
            conn.conn_mode = mode
            return index
        conn = BotConnection(ws_url=ws_url, auto_connect=auto_connect, conn_mode=mode)
        conn.status = "disconnected"
        conn.index = index
        if self.outbound_hook_factory:
            conn.outbound_hook = self.outbound_hook_factory(conn)
        conn.send_hook_registry = self.send_hook_registry
        conn.before_send_hook_registry = self.before_send_hook_registry
        conn.api_hook_registry = self.api_hook_registry
        self.connections[index] = conn
        return index

    async def readd_bot(
        self, ws_url: str, owner_id: int | None, index: int, conn_mode: str | None = None
    ) -> int:
        """更新已有连接的地址（``owner_id`` 同上：兼容保留，已忽略）。"""
        conn = self.connections.get(index)
        if conn:
            conn.ws_url = ws_url
            if conn_mode is not None:
                conn.conn_mode = normalize_mode(conn_mode)
        return index
    async def del_bot(self, index: int) -> None:
        await self.disconnect_bot(index)
        self.connections.pop(index, None)
        self._connect_locks.pop(index, None)  # 清理连接锁，防增删账号后泄漏

    async def connect_bot(self, index: int) -> bool:
        conn = self.connections.get(index)
        if not conn:
            return False
        lock = self._connect_locks.setdefault(index, asyncio.Lock())
        async with lock:  # 防止监督循环与 WebUI 并发双开连接
            if f"{index}" in self.bot_server_tasks:
                conn.status = "connected"
                self.log.info(f"机器人索引: {index} 已有连接")
                return True

            conn.status = "connecting"
            conn.last_error = None
            # 走到这里 = 显式连接请求（WebUI 连接/重连，或监督循环的掉线恢复）：
            # 解除「用户手动断开」的抑制，让该账号重新参与自动重连。
            conn.suppress_auto_reconnect = False
            if is_reverse(self._conn_mode(conn)):
                # 反向：起监听等 OneBot 实现连入（成败不依赖对端是否已在线）
                return await self._listen_reverse(index, conn)

            epoch = getattr(conn, "connect_epoch", 0)
            websocket = await self._connect_websocket(conn.ws_url)
            if getattr(conn, "connect_epoch", 0) != epoch:
                # 握手期间收到了断开请求 → 本次连接已作废，丢弃结果（不要覆盖 disconnected 状态）
                if websocket is not None:
                    with contextlib.suppress(Exception):
                        await websocket.close()
                self.log.info(f"机器人索引: {index} 连接已取消（握手期间收到断开请求）")
                return False
            if websocket:
                conn.websocket = websocket
                conn.status = "connected"
                conn.reconnect_attempts = 0
                self.log.info(f"机器人索引: {index} 连接成功: {mask_ws_url(conn.ws_url)}")
                self.bot_server_tasks[f"{index}"] = asyncio.create_task(
                    self._bot_server(index), name=f"bot_server:{index}"
                )
                await self._notify_status(conn, "connected")
                return True
            conn.reconnect_attempts += 1
            conn.status = "error"
            conn.last_error = f"连接失败: {mask_ws_url(conn.ws_url)}"
            conn._last_connect_attempt = time.time()  # 失败也记账，保证监督循环退避生效
            self.log.error(f"机器人索引: {index} 连接失败: {mask_ws_url(conn.ws_url)}")
            await self._notify_status(conn, "error", conn.last_error)
            return False

    # ==================== 反向 WS（框架监听） ====================
    # 监听器、接入、等待接入的状态收敛都在 ReverseLink 里（见 reverse_link.py）；
    # 这里只留模式分支，让「正向连出」这条主线仍然一眼读完。
    async def _listen_reverse(self, index: int, conn) -> bool:
        return await self.reverse_link.ensure_listening(index, conn)

    async def disconnect_bot(self, index: int, *, manual: bool = False) -> None:
        """断开连接。

        ``manual=True`` = 用户在 WebUI 主动点「断开」（``/api/bots/{index}/disconnect``）：
        置位 ``suppress_auto_reconnect``，监督循环不再把这个账号自动连回来，
        直到用户显式「连接 / 重连」（或重新打开它的自动连接开关 / 重启进程）。

        不加这个标记时，``_supervise`` 会把「status=disconnected 且 auto_connect=True」
        当成掉线，最多 10s 后又连接 —— 日志里表现为「断开连接」之后紧跟「连接成功」，
        即「点了断开，一段时间后又连接」。
        """
        conn = self.connections.get(index)
        if conn is not None:
            if manual:
                conn.suppress_auto_reconnect = True
                self.log.info(f"机器人索引: {index} 用户主动断开：本次运行期间不再自动重连（连接/重连可恢复）")
            # 作废正在握手的 connect_bot（见 connect_bot 的 epoch 校验），
            # 否则握手成功会把刚置好的断开状态覆盖回 connected。
            conn.connect_epoch = getattr(conn, "connect_epoch", 0) + 1

        # 反向监听器随「断开」一并停掉：用户的断开语义包含「不再占端口等人接入」
        await self.reverse_link.stop(index)

        task_key = f"{index}"
        task = self.bot_server_tasks.pop(task_key, None)
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
            self.log.info(f"机器人索引: {index} 断开连接")

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

    async def reconnect_bot(self, index: int) -> bool:
        await self.disconnect_bot(index)
        conn = self.connections.get(index)
        if not conn:
            return False
        conn.reconnect_attempts += 1
        return await self.connect_bot(index)

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
            await self.account_saver(conn.index, conn.bot_id, (conn.login_info or {}).get("nickname", ""))
        except Exception as e:
            self.log.warning(f"[Gateway] 记录上次登录账号失败 (#{conn.index}): {e}")

    async def _get_login_info(self, conn: BotConnection) -> bool:
        try:
            resp = await conn.get_login_info()
            if resp and resp.get("status") == "ok":
                data = resp.get("data", {}) or {}
                conn.bot_id = int(data.get("user_id", 0) or 0)
                conn.login_info = data
                self.log.info(f"登录成功 | #{conn.index} | {conn.bot_id} | {data.get('nickname', '')}")
                await self._record_login_account(conn)
                if self.login_handler:
                    await self.login_handler(conn)
                # 强制重新广播（此时才有真实 bot_id，前端据此刷新模块数据）
                conn._last_notified_status = None
                await self._notify_status(conn, "connected", "已登录")
                self.connect_type = False
                return True
            self.log.info("获取账号信息失败，将尝试从消息流中自动获取 self_id")
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
            self.log.error(f"获取群聊列表失败 (#{conn.index})")
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
    async def _bot_server(self, index: int, *, login_immediately: bool = False) -> None:
        conn = self.connections.get(index)
        if not conn or not conn.websocket:
            return
        bot_logger = self.log.add_info(f"#{index}")
        conn.bot_id = 0
        start_type = False
        login_tasks: list[asyncio.Task] = []
        if login_immediately:
            # 反向连接：OneBot 实现可能十几秒才推第一个事件（心跳/消息），
            # 一直不登录就会让 WebUI 长时间显示「未登录」。握手成功即主动拉一次。
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
            self._dispatch_worker(index, queue), name=f"dispatch_worker:{index}"
        )
        # 「连接级失败响应」在循环内已自行收尾，用它避免退出后再走一遍收尾逻辑
        connection_ended = False

        try:
            async for message in conn.websocket:
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
                        if conn.websocket:
                            try:
                                await conn.websocket.close()
                            except Exception as e:
                                # 关闭失败仅记 debug：连接随后会被置空并由监督循环重建，
                                # 但留痕便于排查"连接未真正释放"类问题
                                logger.add_info(f"#{conn.index}").debug(
                                    f"[Gateway] 关闭异常连接失败（已忽略）: {e}"
                                )
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
            await self._handle_connection_end(conn, index, f"{e}", bot_logger, abnormal=True)
        else:
            if not connection_ended:
                # 关键：websockets 的 Connection.__aiter__ 会把「正常关闭（1000）」的
                # ConnectionClosedOK 直接吞掉、让 async for 正常退出（不抛异常）。
                # 不在这里显式收尾就会留下僵尸连接：status 永远停在 connected →
                # 监督循环判定「在线」永不重连，之后每次发送都抛 ConnectionClosed
                # 立即失败。真实案例：远端只重启约 7 秒，客户端僵死 6 小时以上，
                # 而且日志里连一条断线记录都没有。
                await self._handle_connection_end(
                    conn, index, self._close_reason(conn), bot_logger, abnormal=False
                )
        finally:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
            for t in login_tasks:
                if not t.done():
                    t.cancel()
            self.bot_server_tasks.pop(f"{index}", None)

    async def _handle_connection_end(
        self, conn, index: int, reason: str, bot_logger, *, abnormal: bool
    ) -> None:
        """连接结束的统一收尾：留痕 → 置掉线态 → 广播 → 清 ws → 在途请求立即失败。

        异常断线（ConnectionClosedError 等）与正常关闭（1000）**必须**走同一套收尾：
        前者由 ``except`` 分支触发，后者由 ``async for`` 正常退出的 ``else`` 分支触发。
        少了后者就是「僵尸连接」——状态仍 connected，监督循环永远不重连。
        """
        text = f"WebSocket 连接结束: {reason}"
        if abnormal:
            bot_logger.error(text)
        else:
            # 正常关闭是服务端主动行为（重启/下线）：必须留痕（否则无从发现），
            # 但用 WARNING，不必惊动 errors.log
            bot_logger.warning(text)
        await self._mark_offline(conn, reason)
        if conn.websocket:
            try:
                await conn.websocket.close()
            except Exception as e:
                # 关闭失败仅记 debug：连接随后会被置空并由监督循环重建，但留痕便于排查
                bot_logger.debug(f"[Gateway] 关闭已结束的连接失败（已忽略）: {e}")
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
    def _close_reason(conn) -> str:
        """正常关闭的可读原因：带上 close_code/close_reason，便于定位是哪一端关的。"""
        code = getattr(conn.websocket, "close_code", None)
        reason = getattr(conn.websocket, "close_reason", None)
        if code is None:
            return "连接已结束（未收到关闭帧）"
        suffix = f" {reason}" if reason else ""
        return f"服务端关闭连接 (code={code}{suffix})"

    async def _dispatch_worker(self, index: int, queue: asyncio.Queue) -> None:
        """串行处理该 bot 的事件队列，独立于接收循环。

        单 bot 单 worker，保持与原「接收循环内串行派发」相同的处理顺序；
        接收循环因此始终空闲，可及时消费 API 响应。
        """
        conn = self.connections.get(index)
        if conn is None:
            return
        bot_logger = self.log.add_info(f"#{index}")
        recv_logger = self.log.prefix(f"Recv][#{index}")
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
            # 日志：[接收<-][#idx][群名] 名字:内容（私信无群名）
            text = _message_log_text(event).strip()
            if text:
                name = event.user.card or event.user.nickname or ""
                prefix = f"接收<-] [#{conn.index}"
                if event.event_type == "message_group":
                    group_name = self._lookup_group_name(conn, event.group.group_id)
                    prefix += f"] [{group_name}({event.group.group_id})" if group_name else f"[{event.group.group_id}]"
                name_info = f"{name}({event.user_id})" if name else f"{event.user_id}"
                recv_logger.prefix(prefix).info(f"{name_info}: {text}")
        elif isinstance(event, MessageEvent) and event.event_type == "bot_send_msg":
            # 机器人自己发送的消息不进入 recv，已由 API 发送侧 [发送->] 覆盖
            pass
        elif isinstance(event, BaseEvent):
            if event.event_type not in ("bot_heartbeat",):
                text = _event_text(event)
                if text:
                    # 与消息一致：群事件前缀拼 [群id]（notice 无群名，仅 id）
                    prefix = f"通知<-] [#{conn.index}"
                    group_id = getattr(event, "group_id", 0) or 0
                    if group_id:
                        prefix += f"] [{group_id}"
                    if event_name(event.event_type) == "未知类型":
                        # 未收录的类型只打得出「未知类型 <uid>」这种没信息量的文本。
                        # 端侧排查（线上那条「未知类型 3437542570」实为 SnowLuma 重启前
                        # 推的 session-closed 通知）必须能看到原始 payload，否则只能靠时间猜。
                        text = f"{text} | raw={_raw_payload_text(event)}"
                    recv_logger.prefix(prefix).info(text)

        # 忽略标记检查
        if self._is_ignore(event):
            bot_logger.debug(
                f"[忽略] 命中忽略标记，丢弃事件 {event.event_type}"
                f"（用户 {event.user_id}，连接 #{conn.index}/{conn.bot_id}）"
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
        auto_indices = [
            i for i in list(self.connections.keys())
            if self.connections[i].auto_connect and self.connections[i].status not in ("connected", "connecting")
        ]
        if auto_indices:
            await asyncio.gather(*(self.connect_bot(i) for i in auto_indices))
        if self._supervise_task is None or self._supervise_task.done():
            self._supervise_task = asyncio.create_task(self._supervise(), name="gateway_supervise")

    async def reconcile(self) -> None:
        """按当前配置列表立即收敛连接映射，增删账号后保持索引一致。

        WebUI 在增/删账号后主动调用，避免依赖监督循环（10s 周期）造成索引漂移窗口。
        """
        getter = getattr(self, "bots_provider", None)
        bots_config = getter() if getter else []
        await self._reconcile(bots_config)

    async def _reconcile(self, bots_config: list[dict]) -> None:
        """按 config 序号收敛 gateway 连接映射，消除删除中间账号导致的索引漂移。

        - config 之外的连接 → 断开并移除；
        - 已存在的连接 → 同步 ws_url/owner/auto_connect，url 变更则断开待重连。
        """
        config_count = len(bots_config)
        for index in list(self.connections.keys()):
            if index >= config_count:
                await self.del_bot(index)
        for index, cfg in enumerate(bots_config):
            ws_url = cfg.get("ws_url", "")
            conn = self.connections.get(index)
            if conn is None:
                await self.add_bot(
                    ws_url, None, cfg.get("auto_connect", False), index=index,
                    conn_mode=cfg.get("conn_mode", FORWARD),
                )
                continue
            changed = False
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
                self.log.info(f"机器人索引: {index} 配置已更新: {mask_ws_url(conn.ws_url)}")
                if conn.status in ("connected", "connecting") or self._reverse_listening(index):
                    await self.disconnect_bot(index)  # url/方向变更 → 断开待重连(或待重新监听)

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
                    for index in list(self.connections.keys()):
                        conn = self.connections[index]
                        if not conn.auto_connect or conn.status in ("connected", "connecting"):
                            continue
                        if getattr(conn, "suppress_auto_reconnect", False):
                            # 用户主动断开：只保留「意外掉线」的自动恢复语义，不把它的断开连回来
                            continue
                        if is_reverse(self._conn_mode(conn)) and self._reverse_listening(index):
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
                        await self.connect_bot(index)
                except Exception as e:
                    self.log.error(f"监督循环异常: {e}")

    async def _sweep_zombies(self) -> None:
        """把「状态还写着 connected、连接其实已经没了」的账号改成掉线态，交给重连逻辑。

        正常收尾（``_handle_connection_end`` / ``disconnect_bot``）都会改状态；
        这里兜住没走到收尾的静默路径（接收任务消失、ws 已被对端关闭等）——
        否则监督循环会把它们当成「在线」，永不重连，表现为静默死亡。
        """
        for index, conn in list(self.connections.items()):
            if conn.status != "connected" or not self._connection_gone(index, conn):
                continue
            self.log.warning(f"机器人索引: {index} 连接已不存在但状态仍是 connected，按掉线处理")
            await self._mark_offline(conn, "连接已不存在（僵尸连接，已按掉线处理）")
            if conn.websocket is not None:
                with contextlib.suppress(Exception):
                    await conn.websocket.close()
            conn.websocket = None
            self._reset_conn_state(conn)

    def _connection_gone(self, index: int, conn) -> bool:
        """connected 状态下「连接已消失」的判定（任一命中即视为已消失）。

        调用方（``_sweep_zombies``）已限定只看 status=="connected"：握手中的
        "connecting" 期间 websocket 尚未赋值、任务尚未创建，用本判据会被误判。
        """
        if conn.websocket is None:
            return True
        # 对端关闭后 websockets 会写入 close_code；收尾若没走到，这里能兜住
        if getattr(conn.websocket, "close_code", None) is not None:
            return True
        task = self.bot_server_tasks.get(f"{index}")
        return task is None or task.done()

    async def shutdown(self) -> None:
        if self._supervise_task:
            self._supervise_task.cancel()
        for index in list(self.connections.keys()):
            await self.disconnect_bot(index)


