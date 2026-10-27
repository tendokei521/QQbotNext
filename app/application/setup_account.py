"""首启向导第一步的「唯一默认账号」+ 后台连接重试。

为什么要单独一层（用户反馈的两条硬需求）：

1. **不能每点一次连接就多一个账号**：向导只允许维护**一个**默认账号，
   反复保存只是"改这个账号"，永远不会新增第二个；
2. **没有测试按钮，后台每 2s 试一次**：连接尝试放进后台循环，前端只读"是否已连接"，
   不需要用户一遍遍点。

账号 ID 在向导里可改；改名走「删除旧行 + 建新行 + 搬迁 Agent/模块绑定」，
确保 id 变了但配置不丢。
"""

from __future__ import annotations

import asyncio
import logging

from app.infrastructure.config.config_service import ConfigOp
from app.infrastructure.onebot.endpoint import (
    EndpointError,
    is_reverse,
    normalize_mode,
    parse_endpoint,
)

#: 本地监听（反向）默认端口与路径：首启向导的默认值固定为 ws://127.0.0.1:4001/
LISTEN_DEFAULT_PORT = 4001
LISTEN_DEFAULT_PATH = "/"
#: 向导默认监听地址
DEFAULT_LISTEN_ADDRESS = f"ws://127.0.0.1:{LISTEN_DEFAULT_PORT}{LISTEN_DEFAULT_PATH}"
#: 后台连接重试间隔（秒）
DEFAULT_CONNECT_INTERVAL = 2.0
#: kv 里记录"向导管理的那个账号"
SETUP_ACCOUNT_KV = "setup_default_account_id"

AGENT_CAPABILITY = "agent"


class SetupAccountError(ValueError):
    """向导账号动作失败（消息可直接展示）。"""


def default_listen_address(existing: object = ()) -> str:
    """向导默认监听地址：**固定从 4001 起**，被其它账号占用时才顺延端口。

    与「账号管理」的自动建议（3001 起 + OneBot 反向路径）刻意不同：
    首启向导要求的默认值就是 ``ws://127.0.0.1:4001/``，只有端口确实冲突才避让。
    """
    used: set[int] = set()
    for url in existing or ():
        try:
            endpoint = parse_endpoint(str(url))
        except EndpointError:
            continue
        if endpoint.port:
            used.add(endpoint.port)
    port = LISTEN_DEFAULT_PORT
    while port in used and port < LISTEN_DEFAULT_PORT + 100:
        port += 1
    return f"ws://127.0.0.1:{port}{LISTEN_DEFAULT_PATH}"


def normalize_listen_address(value: str, conn_mode: str = "reverse") -> str:
    """把用户填的地址补成 ws URL。

    默认地址不在这里维护：反向留空时由 ``upsert`` 调用
    ``endpoint.suggest_address``（与账号管理同一来源）按已有账号避让端口自动分配；
    正向留空表示"等用户填对端地址"，保持空串。

    省略 scheme 的写法也接受：``127.0.0.1:3001/onebot/v11/ws``。
    """
    text = str(value or "").strip()
    if not text and is_reverse(conn_mode):
        return DEFAULT_LISTEN_ADDRESS
    if text and "://" not in text:
        text = "ws://" + text.lstrip("/")
    return text


class SetupConnectLoop:
    """后台连接循环：每 ``interval`` 秒尝试一次，连上就自己停。"""

    def __init__(self, bot_service, *, interval: float = DEFAULT_CONNECT_INTERVAL) -> None:
        self.bot_service = bot_service
        self.interval = max(0.05, float(interval))
        self._tasks: dict[str, asyncio.Task] = {}

    def running(self, account_id: str) -> bool:
        task = self._tasks.get(str(account_id))
        return task is not None and not task.done()

    def ensure(self, account_id: str) -> bool:
        """开始（或复用）某账号的连接循环；返回是否新起了一个任务。"""
        key = str(account_id)
        if self.running(key):
            return False
        self._tasks[key] = asyncio.create_task(self._run(key), name=f"setup_connect:{key}")
        return True

    async def _run(self, account_id: str) -> None:
        try:
            while True:
                if not self.bot_service.account_exists(account_id):
                    return  # 账号已删除：别对着空账号每 2s 空转
                if self.bot_service.is_connected(account_id):
                    return
                if self.bot_service.is_listening(account_id):
                    # 反向监听已就绪：端口常驻，不需要继续"连接"（否则每 2s 重复广播状态）
                    return
                await self.bot_service.connect(account_id)
                await asyncio.sleep(self.interval)
        except asyncio.CancelledError:
            raise
        finally:
            self._tasks.pop(account_id, None)

    def stop(self, account_id: str) -> None:
        task = self._tasks.pop(str(account_id), None)
        if task is not None and not task.done():
            task.cancel()

    def stop_all(self) -> list[str]:
        stopped = list(self._tasks)
        for key in stopped:
            self.stop(key)
        return stopped


class SetupAccountService:
    """向导第一步：保证"只有一个默认账号"，并驱动后台连接。"""

    def __init__(self, bot_service, config_service, *, interval: float = DEFAULT_CONNECT_INTERVAL) -> None:
        self.bot_service = bot_service
        self.config_service = config_service
        self.connect_loop = SetupConnectLoop(bot_service, interval=interval)

    # ==================== 查询 ====================

    async def current(self) -> dict | None:
        """当前向导账号（kv 记录优先；只有单个账号时也认它）。"""
        bots = self.config_service.get_bots()
        if not bots:
            return None
        saved = str(await self.config_service.get_kv(SETUP_ACCOUNT_KV, "") or "")
        target = next((bot for bot in bots if str(bot.get("id")) == saved), None)
        if target is None and len(bots) == 1:
            target = bots[0]
            await self.config_service.set_kv(SETUP_ACCOUNT_KV, str(target.get("id")))
        if target is None:
            return None
        return self._describe(target)

    def _describe(self, row: dict) -> dict:
        account_id = str(row.get("id"))
        return {
            "account_id": account_id,
            "name": str(row.get("name") or account_id),
            "ws_url": str(row.get("ws_url") or ""),
            "conn_mode": normalize_mode(row.get("conn_mode")),
            "connected": self.bot_service.is_connected(account_id),
            "connecting": self.connect_loop.running(account_id),
        }

    # ==================== 保存（唯一账号） ====================

    async def upsert(
        self,
        *,
        account_id: str = "",
        name: str = "",
        ws_url: str = "",
        access_token: str = "",
        conn_mode: str = "reverse",
    ) -> dict:
        """保存向导账号：没有就建一个，有就改它（**永远不新增第二个**）。"""
        requested_id = str(account_id or "").strip()
        mode = normalize_mode(conn_mode)

        bots = self.config_service.get_bots()
        current = await self.current()
        current_id = str(current["account_id"]) if current else ""

        # 默认地址与账号管理同源（endpoint.suggest_address），这里不再维护第二份默认值：
        # - 反向必须能监听 → 留空时按已有账号避让端口自动分配；
        # - 正向留空 = 等用户填对端地址（不预填本地地址）。
        address = normalize_listen_address(ws_url, mode)
        if not address and is_reverse(mode):
            existing = [
                str(bot.get("ws_url") or "")
                for bot in bots
                if str(bot.get("id")) != current_id
            ]
            address = default_listen_address(existing)
        if address:
            try:
                parse_endpoint(address, require_port=is_reverse(mode))
            except EndpointError as e:
                label = "监听地址" if is_reverse(mode) else "连接地址"
                raise SetupAccountError(f"{label}不可用：{e}") from e
        if current is None:
            if len(bots) > 1:
                raise SetupAccountError(
                    "已经有多个账号，请到「账号管理」里编辑；首次配置只维护一个默认账号"
                )
            created_id = await self.bot_service.add_bot(
                address, None, True, conn_mode=mode,
                account_id=requested_id or None,
                name=str(name or "").strip(),
            )
            await self.config_service.set_kv(SETUP_ACCOUNT_KV, created_id)
            if address and not self.bot_service.is_connected(created_id):
                self.connect_loop.ensure(created_id)
            return {
                "account_id": created_id,
                "name": self.config_service.get_account_name(created_id),
                "ws_url": address,
                "conn_mode": mode,
                "connected": self.bot_service.is_connected(created_id),
                "connecting": bool(address) and self.connect_loop.running(created_id),
                "created": True,
            }

        old_id = str(current["account_id"])
        target_id = requested_id or old_id
        if target_id != old_id and any(str(bot.get("id")) == target_id for bot in bots):
            raise SetupAccountError(f"账号 ID「{target_id}」已被占用")
        # 改名不是新账号：模块默认开关要跟着搬，否则升级前的老账号会被静默变成「默认关」
        old_modules_default = self.config_service.get_modules_default_enabled(old_id)

        updated = []
        for bot in bots:
            if str(bot.get("id")) != old_id:
                updated.append(bot)
                continue
            row = dict(bot)
            row["id"] = target_id
            row["name"] = str(name or "").strip() or row.get("name") or target_id
            row["ws_url"] = address
            row["conn_mode"] = mode
            row["auto_connect"] = True
            if access_token:
                row["access_token"] = access_token
            updated.append(row)

        await self.config_service.save_bots(updated)
        if target_id != old_id:
            await self._migrate_bindings(old_id, target_id)
            await self.config_service.set_modules_default_enabled(
                target_id, old_modules_default
            )
        await self.config_service.set_kv(SETUP_ACCOUNT_KV, target_id)
        await self.bot_service.gateway.reconcile()
        self.connect_loop.stop(old_id)
        if address and not self.bot_service.is_connected(target_id):
            self.connect_loop.ensure(target_id)
        return {
            "account_id": target_id,
            "name": self.config_service.get_account_name(target_id),
            "ws_url": address,
            "conn_mode": mode,
            "connected": self.bot_service.is_connected(target_id),
            "connecting": bool(address) and self.connect_loop.running(target_id),
            "created": False,
        }

    async def _migrate_bindings(self, old_id: str, new_id: str) -> None:
        """把账号级绑定（Agent + 模块）搬到新 id 下；首启阶段通常为空。"""
        capabilities = {AGENT_CAPABILITY}
        registry = getattr(self.bot_service, "registry", None)
        catalog = getattr(registry, "catalog_entries", None)
        if callable(catalog):
            try:
                for entry in catalog():
                    module_name = str(getattr(entry, "name", "") or "")
                    if module_name:
                        capabilities.add(module_name)
            except Exception as e:  # noqa: BLE001 —— 编目不可用不影响账号保存
                logging.getLogger(__name__).debug(f"[Setup] 读取模块编目失败（仅影响迁移范围）: {e}")
        ops: list[ConfigOp] = []
        for capability in sorted(capabilities):
            row = self.config_service.get_binding(capability, "account", old_id)
            if not row:
                continue
            ops.append(ConfigOp("binding", (capability, "account", new_id), dict(row)))
            ops.append(ConfigOp("binding", (capability, "account", old_id), None))
        if ops:
            await self.config_service.apply_ops(ops)

    def should_retry(self, account_id: str) -> bool:
        """是否还需要后台重试连接。

        已连上、反向监听已就绪、或地址还空着（正向等用户填）都不需要重试——
        否则前端每 2s 轮询一次就会反复新建/唤醒连接循环，白耗 CPU 与日志。
        """
        key = str(account_id or "")
        if not key:
            return False
        if self.bot_service.is_connected(key) or self.bot_service.is_listening(key):
            return False
        row = next(
            (bot for bot in self.config_service.get_bots() if str(bot.get("id")) == key),
            None,
        )
        return bool(row and str(row.get("ws_url") or "").strip())

    # ==================== 生命周期 ====================

    def stop_connecting(self, account_id: str | None = None) -> list[str]:
        if account_id:
            self.connect_loop.stop(account_id)
            return [str(account_id)]
        return self.connect_loop.stop_all()

    async def shutdown(self) -> None:
        self.stop_connecting()
