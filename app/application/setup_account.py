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
from app.infrastructure.onebot.endpoint import EndpointError, is_reverse, normalize_mode, parse_endpoint

#: 向导监听地址的默认值（按要求固定为这个，不做"自动生成"）
DEFAULT_LISTEN_ADDRESS = "127.0.0.1:4001/ws"
#: 后台连接重试间隔（秒）
DEFAULT_CONNECT_INTERVAL = 2.0
#: kv 里记录"向导管理的那个账号"
SETUP_ACCOUNT_KV = "setup_default_account_id"

AGENT_CAPABILITY = "agent"


class SetupAccountError(ValueError):
    """向导账号动作失败（消息可直接展示）。"""


def normalize_listen_address(value: str) -> str:
    """把用户填的地址补成 ws URL：``127.0.0.1:4001/ws`` → ``ws://127.0.0.1:4001/ws``。"""
    text = str(value or "").strip()
    if not text:
        text = DEFAULT_LISTEN_ADDRESS
    if "://" not in text:
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
                if self.bot_service.is_connected(account_id):
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
        address = normalize_listen_address(ws_url)
        mode = normalize_mode(conn_mode)
        try:
            parse_endpoint(address, require_port=is_reverse(mode))
        except EndpointError as e:
            raise SetupAccountError(f"监听地址不可用：{e}") from e

        bots = self.config_service.get_bots()
        current = await self.current()
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
            self.connect_loop.ensure(created_id)
            return {
                "account_id": created_id,
                "name": self.config_service.get_account_name(created_id),
                "ws_url": address,
                "conn_mode": mode,
                "connected": self.bot_service.is_connected(created_id),
                "connecting": True,
                "created": True,
            }

        old_id = str(current["account_id"])
        target_id = requested_id or old_id
        if target_id != old_id and any(str(bot.get("id")) == target_id for bot in bots):
            raise SetupAccountError(f"账号 ID「{target_id}」已被占用")

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
        await self.config_service.set_kv(SETUP_ACCOUNT_KV, target_id)
        await self.bot_service.gateway.reconcile()
        self.connect_loop.stop(old_id)
        self.connect_loop.ensure(target_id)
        return {
            "account_id": target_id,
            "name": self.config_service.get_account_name(target_id),
            "ws_url": address,
            "conn_mode": mode,
            "connected": self.bot_service.is_connected(target_id),
            "connecting": True,
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

    # ==================== 生命周期 ====================

    def stop_connecting(self, account_id: str | None = None) -> list[str]:
        if account_id:
            self.connect_loop.stop(account_id)
            return [str(account_id)]
        return self.connect_loop.stop_all()

    async def shutdown(self) -> None:
        self.stop_connecting()
