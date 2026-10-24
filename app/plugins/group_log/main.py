"""群聊可见事件记录模块：把"群里最近发生过什么"持续记下来，供装配层当环境用。

## 它解决什么

会话历史只在消息**触发**机器人时写入（`pipeline.check_trigger` 不通过就直接 return），
且完全不含互动（谁给谁贴了表情、谁戳了谁、谁撤回了什么）。于是模型对"群里现在什么
气氛"没有任何持续来源。本模块在**事件接收时**记账：

- `message_group`：所有群消息（不依赖是否 @ 我、不依赖 trigger 是否通过）；
- `notice_group_emoji` / `notice_poke` / `notice_group_recall`：互动与撤回；
- `@send_hook`：机器人自己发出的群消息（带 message_id，供去重与"我干过什么"）。

**私聊只记戳一戳**（其余私聊内容不入账）。

## 与"对话记录"的边界

会话历史（`app/agent/session/`）是规范对话形态，本模块**不写、不改**它；
两者是独立的两套内容，装配时由渲染层按 message_id 去重（会话历史优先）。

## 分工

- **本文件**：插件骨架——配置、生命周期、`@module_hook` 接线、存储挂载。
- `service.py`：事件 → `LogEvent` 的翻译口径（脱敏 / 剥控制标记 / 字段映射）。
  那里是纯函数，可单独测；改「记录什么」去那边，改「接哪些事件」改这里。
- `app/agent/group_log/store.py`：记录面（幂等、保留窗口、淘汰、落盘）。

`append_many` 只入队，不阻塞事件分发；异常一律吞掉并留痕，**记录失败绝不影响聊天**。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.agent.group_log.store import (
    DEFAULT_RETENTION_COUNT,
    DEFAULT_RETENTION_HOURS,
    GroupLogStore,
    attach,
    detach,
    store_of,
)
from app.core.logger import logger, account_label
from app.plugin import BaseModule, module_hook, send_hook

from . import service
from .config_schema import SCHEMA
from .service import strip_directives  # noqa: F401 - 对外保持 module.strip_directives 可用


class Module(BaseModule):
    name = "群聊记录"
    sign = "GroupLog"
    description = "持续记录群聊最近可见事件（消息/表情/戳/撤回/我的发言），作为模型的环境背景"
    permission = "everyone"
    category = "消息"
    order = 60
    default_config = {
        "group_log_enable": True,
        "retention_count": DEFAULT_RETENTION_COUNT,
        "retention_hours": DEFAULT_RETENTION_HOURS,
        "strip_directives": True,
    }
    config_schema = SCHEMA

    # ==================== 生命周期 ====================

    async def on_load(self) -> None:
        self._store = self._make_store()
        try:
            await self._store.start()
        except Exception as e:  # noqa: BLE001 - 存储不可用不应拖垮模块加载
            logger.add_info(account_label(self.bot_id)).warning(f"[GroupLog] 启动存储失败（降级为空）: {e}")
        runtime = self._runtime()
        if runtime is not None:
            attach(runtime, self._store)
        logger.add_info(account_label(self.bot_id)).debug("[GroupLog] 群聊记录已就绪")

    async def on_unload(self) -> None:
        runtime = self._runtime()
        if runtime is not None:
            detach(runtime)
        store = getattr(self, "_store", None)
        if store is not None:
            try:
                await store.close()
            except Exception as e:  # noqa: BLE001
                logger.add_info(account_label(self.bot_id)).debug(f"[GroupLog] 关闭存储异常（忽略）: {e}")
        self._store = None

    # ==================== 内部工具 ====================

    def _runtime(self) -> Any:
        services = getattr(self.ctx, "services", None)
        manager = getattr(services, "agent_manager", None)
        if manager is None or self.bot_id is None:
            return None
        try:
            return manager.get_runtime(self.bot_id)
        except Exception as e:  # noqa: BLE001
            logger.add_info(account_label(self.bot_id)).debug(f"[GroupLog] 取 runtime 失败: {e}")
            return None

    def _data_dir(self) -> Path | None:
        settings = getattr(self.ctx.services, "settings", None)
        base = getattr(settings, "module_data_dir", None)
        if base is None:
            return None
        return Path(base) / self.module_name

    def _cfg(self, key: str, default: Any = None) -> Any:
        try:
            return self.config.get(key, default)
        except Exception:  # noqa: BLE001
            return default

    def _make_store(self) -> GroupLogStore:
        try:
            retention_count = int(self._cfg("retention_count", DEFAULT_RETENTION_COUNT) or 0)
        except (TypeError, ValueError):
            retention_count = DEFAULT_RETENTION_COUNT
        try:
            retention_hours = float(self._cfg("retention_hours", DEFAULT_RETENTION_HOURS))
        except (TypeError, ValueError):
            retention_hours = DEFAULT_RETENTION_HOURS
        return GroupLogStore(
            bot_id=self.bot_id,
            data_dir=self._data_dir(),
            retention_count=retention_count,
            retention_hours=retention_hours,
        )

    def _store_or_none(self):
        store = getattr(self, "_store", None)
        if store is not None:
            return store
        runtime = self._runtime()
        return store_of(runtime) if runtime is not None else None

    def _record(self, events) -> None:
        """写入记录面：任何异常都吞掉（记录失败不能影响聊天）。"""
        if not events:
            return
        store = self._store_or_none()
        if store is None:
            return
        try:
            store.append_many(events)
        except Exception as e:  # noqa: BLE001
            logger.add_info(account_label(self.bot_id)).debug(f"[GroupLog] 记录失败（忽略）: {e}")

    def _enabled(self) -> bool:
        return bool(self._cfg("group_log_enable", True))

    # ==================== 事件接入（翻译口径见 service.py） ====================

    @module_hook("message_group", order=10)
    async def _on_group_message(self, event) -> None:
        if not self._enabled():
            return
        self._record(service.group_message_event(
            event, strip=bool(self._cfg("strip_directives", True))
        ))

    @module_hook("notice_group_emoji", order=10)
    async def _on_emoji(self, event) -> None:
        if not self._enabled():
            return
        self._record(service.emoji_event(event))

    @module_hook("notice_poke", order=10)
    async def _on_poke(self, event) -> None:
        """戳一戳：**群聊与私聊都记**（私聊唯一入账的互动）。"""
        if not self._enabled():
            return
        self._record(service.poke_event(event))

    @module_hook("notice_group_recall", order=10)
    async def _on_recall(self, event) -> None:
        if not self._enabled():
            return
        self._record(service.recall_event(event))

    @send_hook(message_type="group", order=10)
    async def _on_my_send(self, ctx) -> None:
        """机器人自己发出的群消息：记下来才能做"我做过什么"的反馈与去重。"""
        if not self._enabled():
            return
        self._record(service.my_send_event(
            ctx,
            bot_id=self.bot_id,
            strip=bool(self._cfg("strip_directives", True)),
        ))
