"""群聊可见事件记录模块：把"群里最近发生过什么"持续记下来，供装配层当环境用。

## 它解决什么

会话历史只在消息**触发**机器人时写入（``pipeline.check_trigger`` 不通过就直接 return），
且完全不含互动（谁给谁贴了表情、谁戳了谁、谁撤回了什么）。于是模型对"群里现在什么
气氛"没有任何持续来源。本模块在**事件接收时**记账：

- ``message_group``：所有群消息（不依赖是否 @ 我、不依赖 trigger 是否通过）；
- ``notice_group_emoji`` / ``notice_poke`` / ``notice_group_recall``：互动与撤回；
- ``@send_hook``：机器人自己发出的群消息（带 message_id，供去重与"我干过什么"）。

**私聊只记戳一戳**（其余私聊内容不入账）。

## 与"对话记录"的边界

会话历史（``app/llm/session.py``）是规范对话形态，本模块**不写、不改**它；
两者是独立的两套内容，装配时由渲染层按 message_id 去重（会话历史优先）。

## 写入口径

- 幂等、保留窗口、淘汰、落盘全在 ``app/llm/group_log/store.py``（记录面）；
- 本模块只负责"事件 → LogEvent"的翻译与脱敏；
- ``append_many`` 只入队，不阻塞事件分发；异常一律吞掉并留痕，
  记录失败绝不影响聊天。
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from app.core.logger import logger
from app.llm.group_log.events import (
    KIND_EMOJI,
    KIND_MESSAGE,
    KIND_MY_SEND,
    KIND_POKE,
    KIND_RECALL,
    LogEvent,
    make_scope,
)
from app.llm.group_log.store import (
    DEFAULT_RETENTION_COUNT,
    DEFAULT_RETENTION_HOURS,
    GroupLogStore,
    attach,
    detach,
    store_of,
)
from app.modules import BaseModule, module_hook, send_hook

from .config_schema import SCHEMA

#: 控制标记剥离：群里有人发 ``[reply]`` / ``<type=...>`` 时，
#: 这些词进环境块会与"输出侧指令"或既有标签语法撞车，甚至变成伪指令
_DIRECTIVE_RE = re.compile(
    r"<type=[^>]{0,32}>|\[reply\]|\[@\s*\d{1,15}\s*\]|\[id\s*\d{1,20}\]|\[↩[^\]]{0,64}\]|\[♡[^\]]{0,32}\]",
    re.IGNORECASE,
)


def strip_directives(text: str) -> str:
    """剥掉会与指令/标签撞车的控制形态，保留正常聊天内容。"""
    return _DIRECTIVE_RE.sub("", str(text or "")).strip()


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
            logger.add_info(f"#{self.bot_id}").warning(f"[GroupLog] 启动存储失败（降级为空）: {e}")
        runtime = self._runtime()
        if runtime is not None:
            attach(runtime, self._store)
        logger.add_info(f"#{self.bot_id}").debug("[GroupLog] 群聊记录已就绪")

    async def on_unload(self) -> None:
        runtime = self._runtime()
        if runtime is not None:
            detach(runtime)
        store = getattr(self, "_store", None)
        if store is not None:
            try:
                await store.close()
            except Exception as e:  # noqa: BLE001
                logger.add_info(f"#{self.bot_id}").debug(f"[GroupLog] 关闭存储异常（忽略）: {e}")
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
            logger.add_info(f"#{self.bot_id}").debug(f"[GroupLog] 取 runtime 失败: {e}")
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

    def _record(self, events: list[LogEvent]) -> None:
        """写入记录面：任何异常都吞掉（记录失败不能影响聊天）。"""
        if not events:
            return
        store = self._store_or_none()
        if store is None:
            return
        try:
            store.append_many(events)
        except Exception as e:  # noqa: BLE001
            logger.add_info(f"#{self.bot_id}").debug(f"[GroupLog] 记录失败（忽略）: {e}")

    def _text_of(self, event: Any) -> str:
        """事件 → 可读正文（复用既有段渲染器：图片/表情/语音都是占位符）。"""
        from app.llm.group_context import extract_msg_text

        try:
            text = extract_msg_text(
                getattr(event, "message", None),
                None,
                False,
                str(getattr(event, "message_id", "") or ""),
                None,
            )
        except Exception as e:  # noqa: BLE001 - 渲染失败退化为空文本，仍记录事件
            logger.add_info(f"#{self.bot_id}").debug(f"[GroupLog] 正文渲染失败: {e}")
            text = ""
        text = text or str(getattr(event, "raw_message", "") or "")
        if bool(self._cfg("strip_directives", True)):
            text = strip_directives(text)
        return text

    def _sender_label(self, event: Any, user_id: Any) -> str:
        """昵称（写入即脱敏，与会话历史的 mask_nickname 同口径）。"""
        from app.llm.group_context import safe_sender_label

        user = getattr(event, "user", None)
        raw = getattr(user, "card", "") or getattr(user, "nickname", "") or ""
        return safe_sender_label(str(raw or "")) if raw else ""

    @staticmethod
    def _reply_to(event: Any) -> str:
        for seg in getattr(event, "message", None) or []:
            stype = seg.get("type") if isinstance(seg, dict) else getattr(seg, "type", "")
            data = (seg.get("data") if isinstance(seg, dict) else getattr(seg, "data", {})) or {}
            if stype == "reply":
                return str(data.get("id", "") or data.get("message_id", "") or "")
        return ""

    # ==================== 事件接入 ====================

    @module_hook("message_group", order=10)
    async def _on_group_message(self, event) -> None:
        if not bool(self._cfg("group_log_enable", True)):
            return
        group = getattr(event, "group", None)
        group_id = str(getattr(group, "group_id", "") or getattr(event, "group_id", "") or "")
        scope = make_scope(True, group_id=group_id)
        if not scope:
            return
        user_id = str(getattr(event, "user_id", "") or "")
        self._record([LogEvent(
            ts=int(getattr(event, "time", 0) or 0),
            kind=KIND_MESSAGE,
            scope=scope,
            group_id=group_id,
            message_id=str(getattr(event, "message_id", "") or ""),
            user_id=user_id,
            nickname=self._sender_label(event, user_id),
            text=self._text_of(event),
            reply_to=self._reply_to(event),
            by_me=bool(user_id and user_id == str(getattr(event, "self_id", "") or "")),
        )])

    @module_hook("notice_group_emoji", order=10)
    async def _on_emoji(self, event) -> None:
        if not bool(self._cfg("group_log_enable", True)):
            return
        group_id = str(getattr(event, "group_id", "") or "")
        scope = make_scope(True, group_id=group_id)
        if not scope:
            return
        user_id = str(getattr(event, "user_id", "") or "")
        likes = getattr(event, "emoji_likes", None) or []
        if not likes:
            # 没有表情明细时仍记一条"有人做了回应"，避免整段互动丢失
            likes = [{"emoji_id": ""}]
        events = []
        for item in likes:
            emoji_id = str((item or {}).get("emoji_id", "") or "") if isinstance(item, dict) else ""
            events.append(LogEvent(
                ts=int(getattr(event, "time", 0) or 0),
                kind=KIND_EMOJI,
                scope=scope,
                group_id=group_id,
                message_id=str(getattr(event, "message_id", "") or ""),
                user_id=user_id,
                nickname=self._sender_label(event, user_id),
                payload={
                    "emoji_id": emoji_id,
                    "is_add": bool(getattr(event, "emoji_is_add", True)),
                    "count": (item or {}).get("count", 1) if isinstance(item, dict) else 1,
                },
                by_me=bool(user_id and user_id == str(getattr(event, "self_id", "") or "")),
            ))
        self._record(events)

    @module_hook("notice_poke", order=10)
    async def _on_poke(self, event) -> None:
        """戳一戳：**群聊与私聊都记**（私聊唯一入账的互动）。"""
        if not bool(self._cfg("group_log_enable", True)):
            return
        group_id = str(getattr(event, "group_id", "") or "")
        is_group = bool(group_id)
        operator = str(getattr(event, "operator_id", "") or getattr(event, "user_id", "") or "")
        target = str(getattr(event, "target_id", "") or "")
        scope = make_scope(
            is_group,
            group_id=group_id,
            user_id=operator if not is_group else None,
        )
        if not scope:
            return
        self._record([LogEvent(
            ts=int(getattr(event, "time", 0) or 0),
            kind=KIND_POKE,
            scope=scope,
            group_id=group_id,
            user_id=operator,
            nickname=self._sender_label(event, operator),
            payload={"target_id": target},
            by_me=bool(operator and operator == str(getattr(event, "self_id", "") or "")),
        )])

    @module_hook("notice_group_recall", order=10)
    async def _on_recall(self, event) -> None:
        if not bool(self._cfg("group_log_enable", True)):
            return
        group_id = str(getattr(event, "group_id", "") or "")
        scope = make_scope(True, group_id=group_id)
        if not scope:
            return
        operator = str(getattr(event, "operator_id", "") or getattr(event, "user_id", "") or "")
        self._record([LogEvent(
            ts=int(getattr(event, "time", 0) or 0),
            kind=KIND_RECALL,
            scope=scope,
            group_id=group_id,
            message_id=str(getattr(event, "message_id", "") or ""),
            user_id=operator,
            nickname=self._sender_label(event, operator),
            by_me=bool(operator and operator == str(getattr(event, "self_id", "") or "")),
        )])

    @send_hook(message_type="group", order=10)
    async def _on_my_send(self, ctx) -> None:
        """机器人自己发出的群消息：记下来才能做"我做过什么"的反馈与去重。"""
        if not bool(self._cfg("group_log_enable", True)):
            return
        group_id = str(getattr(ctx, "group_id", "") or "")
        scope = make_scope(True, group_id=group_id)
        if not scope:
            return
        text = self._outbound_text(getattr(ctx, "params", {}) or {})
        if bool(self._cfg("strip_directives", True)):
            text = strip_directives(text)
        self._record([LogEvent(
            ts=int(time.time()),
            kind=KIND_MY_SEND,
            scope=scope,
            group_id=group_id,
            message_id=str(getattr(ctx, "message_id", "") or ""),
            user_id=str(self.bot_id or ""),
            nickname="我",
            text=text,
            by_me=True,
        )])

    @staticmethod
    def _outbound_text(params: dict) -> str:
        """发送参数 → 可读文本（兼容 str / 段列表 / Message 对象）。"""
        from app.infrastructure.onebot.client import _message_preview

        try:
            return str(_message_preview(params.get("message")) or "")
        except Exception:  # noqa: BLE001
            return str(params.get("message") or "")
