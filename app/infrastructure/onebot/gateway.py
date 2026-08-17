"""OneBot WebSocket 网关。

替代原 webserver/ws_connect.py + ws_botserver.py：
- 管理多账号连接 / 重连 / 断开；
- 消息循环：API 响应消费 → payload 解码 → 转发消息拉取 → 多 Bot 同步去重 → 日志 → 派发；
- 登录后通过注入的 login_handler 触发模块加载（解耦 infrastructure 与 modules）。

注意：多账号同群消息去重逻辑（wait_for_message / _eventable）自原项目忠实移植。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Any, Awaitable, Callable

import websockets

from app.core.event_bus import BotLifecycleEvent, event_bus
from app.core.logger import logger, websocket_logger
from app.domain.events import BaseEvent, MessageEvent, NoticeEvent, RequestEvent
from app.infrastructure.cache import Cache
from app.infrastructure.config.config_service import mask_ws_url, split_ws_url
from app.infrastructure.onebot.client import BotConnection
from app.infrastructure.onebot.codec import decode, event_name

LoginHandler = Callable[[BotConnection], Awaitable[None]]


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
        self.connect_type = False
        self._supervise_task: asyncio.Task | None = None
        self._connect_locks: dict[int, asyncio.Lock] = {}  # 防止监督循环与 WebUI 并发双开
        # 出站拦截钩子工厂（bootstrap 注入）：传入连接 → 返回 (action, params) 钩子
        self.outbound_hook_factory = None
        # 插件钩子注册表（bootstrap 注入）
        self.send_hook_registry = None
        self.before_send_hook_registry = None
        self.api_hook_registry = None
        self.lifecycle_hook_registry = None

    # ==================== 对外查询 ====================
    def get_bots_info(self) -> list[dict]:
        result: list[dict] = []
        for index, conn in sorted(self.connections.items()):
            base, _ = split_ws_url(conn.ws_url)
            result.append({
                "index": conn.index,
                "bot_id": conn.bot_id,
                "owner_id": conn.owner_id,
                "status": conn.status,
                "ws_url": base,  # 对外只暴露基础地址（access_token 独立字段，不回显）
                "login_info": conn.login_info,
                "reconnect_attempts": conn.reconnect_attempts,
                "last_error": conn.last_error,
                "auto_connect": conn.auto_connect,
            })
        return result

    def get_bot_info_by_index(self, index: int) -> dict | None:
        conn = self.connections.get(index)
        if not conn:
            return None
        base, _ = split_ws_url(conn.ws_url)
        return {
            "bot_id": conn.bot_id,
            "owner_id": conn.owner_id,
            "status": conn.status,
            "login_info": conn.login_info,
            "ws_url": base,
        }

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
        self, ws_url: str, owner_id: int | None, auto_connect: bool = False, index: int | None = None
    ) -> int:
        """新增（或按显式 index 更新）一个 Bot 连接。index 缺省取最大索引 + 1（避免删除中间账号后撞已有连接）。"""
        if index is None:
            index = max(self.connections, default=-1) + 1
        conn = self.connections.get(index)
        if conn is not None:  # 已存在 → 仅更新配置字段，不重建连接
            conn.ws_url = ws_url
            conn.owner_id = owner_id
            conn.auto_connect = auto_connect
            return index
        conn = BotConnection(owner_id=owner_id, ws_url=ws_url, auto_connect=auto_connect)
        conn.status = "disconnected"
        conn.index = index
        if self.outbound_hook_factory:
            conn.outbound_hook = self.outbound_hook_factory(conn)
        conn.send_hook_registry = self.send_hook_registry
        conn.before_send_hook_registry = self.before_send_hook_registry
        conn.api_hook_registry = self.api_hook_registry
        self.connections[index] = conn
        return index

    async def readd_bot(self, ws_url: str, owner_id: int | None, index: int) -> int:
        conn = self.connections.get(index)
        if conn:
            conn.ws_url = ws_url
            conn.owner_id = owner_id
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
            websocket = await self._connect_websocket(conn.ws_url)
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

    async def disconnect_bot(self, index: int) -> None:
        conn = self.connections.get(index)
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
        try:
            return await asyncio.wait_for(
                websockets.connect(
                    url,
                    ping_interval=self.settings.ws_ping_interval,
                    ping_timeout=self.settings.ws_ping_timeout,
                ),
                timeout=self.settings.ws_connect_timeout,
            )
        except asyncio.TimeoutError:
            self.log.error(f"WebSocket连接超时（{self.settings.ws_connect_timeout}秒）: {mask_ws_url(url)}")
        except ConnectionRefusedError:
            self.log.error(f"WebSocket连接被拒绝（服务未启动或地址错误）: {mask_ws_url(url)}")
        except Exception as e:
            self.log.error(f"WebSocket连接失败: {mask_ws_url(url)} {e}")
        return None

    # ==================== 登录信息 ====================
    async def _get_login_info(self, conn: BotConnection) -> bool:
        try:
            resp = await conn.get_login_info()
            if resp and resp.get("status") == "ok":
                data = resp.get("data", {}) or {}
                conn.bot_id = int(data.get("user_id", 0) or 0)
                conn.login_info = data
                self.log.info(f"登录成功 | #{conn.index} | {conn.bot_id} | {data.get('nickname', '')}")
                if self.login_handler:
                    await self.login_handler(conn)
                # 强制重新广播（此时才有真实 bot_id，前端据此刷新模块数据）
                conn._last_notified_status = None
                await self._notify_status(conn, "connected", "已登录")
                self.connect_type = False
                return True
            self.log.info(f"获取账号信息失败，将尝试从消息流中自动获取 self_id")
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
    async def _bot_server(self, index: int) -> None:
        conn = self.connections.get(index)
        if not conn or not conn.websocket:
            return
        bot_logger = self.log.add_info(f"#{index}")
        conn.bot_id = 0
        start_type = False
        login_tasks: list[asyncio.Task] = []
        # 事件处理 worker：接收循环只负责「解析 + API 响应匹配 + 入队」，
        # 耗时处理（模块调用 / 转发拉取 / 多 Bot 去重）在 worker 串行执行。
        # 若不这样拆分，模块内 await API 响应（如 send_private_msg 等 echo）时，
        # 接收循环被阻塞、无法消费该响应 → 请求/响应自锁 → 10s 超时。
        queue: asyncio.Queue = asyncio.Queue(maxsize=1024)  # 有界背压，防异常洪峰撑爆内存
        worker = asyncio.create_task(
            self._dispatch_worker(index, queue), name=f"dispatch_worker:{index}"
        )

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
                            except Exception:
                                pass
                            conn.websocket = None
                        self._reset_conn_state(conn)
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
            bot_logger.error(f"WebSocket 连接结束: {e}")
            conn.status = "error"
            conn.last_error = str(e)
            await self._notify_status(conn, "error", str(e))
            if conn.websocket:
                try:
                    await conn.websocket.close()
                except Exception:
                    pass
                conn.websocket = None
            # 清空登录态 + 让在途 API 请求立即失败（与 disconnect_bot 一致）
            self._reset_conn_state(conn)
        finally:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
            for t in login_tasks:
                if not t.done():
                    t.cancel()
            self.bot_server_tasks.pop(f"{index}", None)

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
        """单条事件处理（原接收循环第 3~6 步）：转发拉取 / 日志 / 去重 / 忽略 / 派发。"""
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
                    recv_logger.prefix(prefix).info(text)

        # 群消息多 Bot 同步（仅当同群多 Bot 时等待全部收到，避免重复响应）
        if isinstance(event, MessageEvent) and event.group.group_id and event.event_type == "message_group":
            indexes = await self._wait_for_message(event, conn)
            if indexes is None:
                return

        # 忽略标记检查
        if self._is_ignore(event):
            return

        # 派发
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

    # ==================== 多 Bot 同步去重（移植） ====================
    async def _message_indexes(self, event: MessageEvent) -> list[int]:
        group_id = event.group.group_id
        indexes = []
        for conn in self.connections.values():
            if not conn.all_group_list:
                continue
            if group_id in conn.all_group_list:
                indexes.append(conn.index)
        return indexes

    async def _wait_for_message(self, event: MessageEvent, conn: BotConnection):
        """同群多 Bot 消息去重：有界等待其它 bot 到达，恰好一个 bot 处理。

        - 无 bot 跟踪该群（启动初期/新群）→ 直接放行，绝不丢消息；
        - 单 bot → 立即放行；
        - 多 bot → 等待同群其它 bot（最多 wait_timeout 秒），
          由最先完成的 bot 通过 _done 标记抢到处理权，其余跳过。

        key 设计：base_key 含 message_id（一条消息唯一，同群同秒多条不混淆），
        slot 用固定键存储各 bot 到达标记，所有 bot 读写同一个 slot/done_key，
        避免各 bot 独立计数导致 slot 分裂、双双超时成为处理者。
        """
        msgtime = event.time
        group_id = event.group.group_id
        message_id = event.message_id
        bot_index = conn.index
        indexes = await self._message_indexes(event)
        if not indexes:
            return [bot_index]  # 无跟踪 → 放行（不丢弃）

        base_key = f"{msgtime}_{group_id}_{message_id}"
        done_key = f"{base_key}_done"
        cache = self.cache
        wait_timeout = 1.5

        # 创建到达标记 slot（同步段无 await，并发安全）
        if not cache.has(base_key):
            index_data = {f"{i}": False for i in indexes}
            cache.set(base_key, index_data, 3)

        data = cache.get(base_key)
        if isinstance(data, dict) and data.get(f"{bot_index}") is False:
            data[f"{bot_index}"] = True
            cache.set(base_key, data, 3)

        deadline = time.monotonic() + wait_timeout
        while time.monotonic() < deadline:
            if cache.has(done_key):
                return None  # 已有 bot 处理 → 跳过
            data = cache.get(base_key)
            if isinstance(data, dict) and all(data.values()):
                # 全到齐：抢 _done（单事件循环内 check+set 原子，恰好一个成功）
                if not cache.has(done_key):
                    cache.set(done_key, True, 3)
                    return indexes
                return None
            await asyncio.sleep(0.05)

        # 超时：成为处理者，补全标记避免其它 bot 继续空等
        if not cache.has(done_key):
            cache.set(done_key, True, 3)
            data = cache.get(base_key)
            if isinstance(data, dict):
                for i in list(data.keys()):
                    data[i] = True
                cache.set(base_key, data, 3)
            return indexes
        return None

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
                await self.add_bot(ws_url, cfg.get("owner_id"), cfg.get("auto_connect", False), index=index)
                continue
            changed = False
            if cfg.get("ws_url") != conn.ws_url:
                conn.ws_url = ws_url
                changed = True
            if cfg.get("owner_id") != conn.owner_id:
                conn.owner_id = cfg.get("owner_id")
                changed = True
            if bool(cfg.get("auto_connect", False)) != conn.auto_connect:
                conn.auto_connect = bool(cfg.get("auto_connect", False))
                changed = True
            if changed:
                self.log.info(f"机器人索引: {index} 配置已更新: {mask_ws_url(conn.ws_url)}")
                if conn.status in ("connected", "connecting"):
                    await self.disconnect_bot(index)  # url 变更 → 断开待重连

    async def _supervise(self) -> None:
        """定期收敛连接映射，并以指数退避重连掉线的 auto_connect 账号。"""
        while True:
            await asyncio.sleep(10)
            getter = getattr(self, "bots_provider", None)
            if getter:
                try:
                    bots_config = getter()  # 同步 provider（ConfigService.get_bots）
                    await self._reconcile(bots_config)
                    for index in list(self.connections.keys()):
                        conn = self.connections[index]
                        if not conn.auto_connect or conn.status in ("connected", "connecting"):
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

    async def shutdown(self) -> None:
        if self._supervise_task:
            self._supervise_task.cancel()
        for index in list(self.connections.keys()):
            await self.disconnect_bot(index)


def _message_log_text(event: MessageEvent) -> str:
    """消息的可读日志摘要（仅影响日志显示，不影响传输/派发）。

    - text 段：原样拼接；
    - at / reply 短字段：带值 [at:qq] [reply:id]；
    - image / video 等长字段：仅保留类型 [image] [video]。
    """
    parts = []
    for seg in event.message:
        if seg.type == "text":
            parts.append(seg.data.get("text", ""))
        elif seg.type in ("at", "reply"):
            key = "qq" if seg.type == "at" else "id"
            parts.append(f"[{seg.type}:{seg.data.get(key, '')}]")
        else:
            parts.append(f"[{seg.type}]")
    return "".join(parts)


def _event_text(event: BaseEvent) -> str:
    """通知/申请类事件的动作描述（用于日志；群 id 由调用方拼进前缀）。"""
    t = event.event_type
    uid = event.user_id or 0
    if isinstance(event, NoticeEvent):
        if t == "notice_poke":
            operator = getattr(event, "operator_id", 0) or uid
            target = getattr(event, "target_id", 0) or 0
            return f"{operator}戳了{target}" if target else f"{operator}戳了"
        if t in ("notice_group_recall", "notice_private_recall"):
            op = getattr(event, "operator_id", 0) or uid
            return f"{op}撤回了一条消息"
        if t == "notice_group_emoji":
            emoji_ids = [
                str(e.get("emoji_id", ""))
                for e in (getattr(event, "emoji_likes", []) or [])
                if isinstance(e, dict) and e.get("emoji_id")
            ]
            suffix = f" id:{','.join(emoji_ids)}" if emoji_ids else ""
            return f"{uid}回应了表情{suffix}"
        if t == "notice_group_increase":
            return f"{uid}入群"
        if t == "notice_group_decrease":
            return f"{uid}退群"
        return f"{event_name(t)} {uid}"
    if isinstance(event, RequestEvent):
        if t == "request_group":
            return f"{uid}申请加群"
        if t == "request_private":
            return f"{uid}申请加好友"
        return f"{event_name(t)} {uid}"
    return f"{event_name(t)} {uid}"
