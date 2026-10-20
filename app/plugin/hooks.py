"""装饰器风格的钩子声明。

- ``@module_hook``：模块流水线事件钩子（按事件类型注册处理函数）。
- ``@llm_hook``：LLM 流水线阶段钩子（pre_request / post_response / pre_send / post_send）。
- ``@send_hook``：消息发送成功后钩子（按 bot 与消息类型匹配，回调收到 ``SendContext``）。
- ``@before_send_hook``：任意消息发送前钩子（可改写参数 / 拦截发送）。
- ``@api_hook``：任意 OneBot API 调用后钩子（可观察成功与失败）。
- ``@bot_lifecycle_hook``：Bot 生命周期钩子（login / connected / disconnected / error）。
- ``@event_completed_hook``：入站事件模块链处理完成钩子。
- ``@tool_call_hook``：LLM 工具调用后钩子（装饰器元数据由 BaseModule 收集）。

装饰器本身只把元数据挂到函数对象上，真正的注册由 ``BaseModule.collect_*()``
和 ``ModuleRegistry`` 在模块加载时完成。
"""

from __future__ import annotations

import asyncio
import fnmatch
from dataclasses import dataclass, field
from typing import Any, Callable

from app.core.logger import logger


@dataclass
class SendContext:
    """消息发送成功后的上下文。

    插件在 ``@send_hook`` 回调中接收本对象：
    - ``ctx.message_id``：发送成功响应里的 ``data.message_id``；
    - ``ctx.bot`` / ``ctx.bot_id``：当前发送的 Bot 连接；
    - ``ctx.action`` / ``ctx.params`` / ``ctx.response``：OneBot 发送动作与完整响应；
    - ``ctx.message_type`` / ``ctx.group_id`` / ``ctx.user_id``：发送目标信息。
    """

    message_id: int
    bot: Any = None
    bot_id: int | None = None
    action: str = ""
    params: dict = field(default_factory=dict)
    response: dict = field(default_factory=dict)
    message_type: str = ""
    group_id: int | None = None
    user_id: int | None = None


@dataclass
class BeforeSendContext:
    """消息发送前的上下文。

    - 修改 ``ctx.params`` 可改写发送参数；
    - 设置 ``ctx.skip = True`` 可拦截本次发送（不会真正发出）。
    """

    action: str = ""
    params: dict = field(default_factory=dict)
    bot: Any = None
    bot_id: int | None = None
    message_type: str = ""
    group_id: int | None = None
    user_id: int | None = None
    skip: bool = False


@dataclass
class ApiContext:
    """任意 OneBot API 调用后的上下文。

    - ``ctx.success``：是否为 ``status == "ok"``；
    - ``ctx.message_id``：如果是发送类 API 且有 message_id，则为对应值。
    """

    action: str = ""
    params: dict = field(default_factory=dict)
    response: dict | None = None
    bot: Any = None
    bot_id: int | None = None
    success: bool = False
    message_id: int | None = None
    message_type: str = ""
    group_id: int | None = None
    user_id: int | None = None


@dataclass
class LifecycleContext:
    """Bot 生命周期上下文。

    ``state`` 取值：``login`` / ``connected`` / ``disconnected`` / ``error``。
    """

    state: str = ""
    bot: Any = None
    bot_id: int | None = None
    index: int | None = None
    detail: str = ""


@dataclass
class EventCompletedContext:
    """入站事件模块链处理完成后的上下文。"""

    event: Any = None
    bot: Any = None
    bot_id: int | None = None
    duration_ms: float = 0.0
    state: dict = field(default_factory=dict)


def module_hook(event_type: str = "*", order: int = 100) -> Callable:
    """注册模块流水线钩子。

    Args:
        event_type: 订阅的事件类型，如 ``"message_group"`` / ``"message_private"`` / ``"*"``。
        order: 同一模块内多个钩子的执行顺序，越小越先执行。
    """

    def decorator(fn):
        metas = getattr(fn, "__module_hook_meta__", [])
        metas.append({"event_type": event_type, "order": order})
        setattr(fn, "__module_hook_meta__", metas)
        return fn

    return decorator


def llm_hook(stage: str, event_type: str = "*", order: int = 100) -> Callable:
    """注册 LLM 流水线钩子。

    Args:
        stage: LLM 流水线阶段，取值：
            - ``pre_request``：LLM 请求前，可暂停/防抖/合并；
            - ``post_response``：LLM 返回后，可拆分/清洗/改写；
            - ``pre_send``：每条消息发送前；
            - ``post_send``：每条消息发送后。
        event_type: 只对指定事件类型生效，``"*"`` 表示全部。
        order: 同一阶段内多个钩子的执行顺序，越小越先执行。
    """

    def decorator(fn):
        metas = getattr(fn, "__llm_hook_meta__", [])
        metas.append({"stage": stage, "event_type": event_type, "order": order})
        setattr(fn, "__llm_hook_meta__", metas)
        return fn

    return decorator


def send_hook(message_type: str = "*", order: int = 100) -> Callable:
    """注册消息发送成功后钩子。

    Args:
        message_type: 只对指定发送类型生效，``"group"`` / ``"private"`` / ``"*"``。
        order: 同一模块内多个发送钩子的执行顺序，越小越先执行。
    """

    def decorator(fn):
        metas = getattr(fn, "__send_hook_meta__", [])
        metas.append({"message_type": message_type, "order": order})
        setattr(fn, "__send_hook_meta__", metas)
        return fn

    return decorator


def before_send_hook(message_type: str = "*", order: int = 100) -> Callable:
    """注册消息发送前钩子。

    Args:
        message_type: 只对指定发送类型生效，``"group"`` / ``"private"`` / ``"*"``。
        order: 同一模块内多个钩子的执行顺序，越小越先执行。
    """

    def decorator(fn):
        metas = getattr(fn, "__before_send_hook_meta__", [])
        metas.append({"message_type": message_type, "order": order})
        setattr(fn, "__before_send_hook_meta__", metas)
        return fn

    return decorator


def api_hook(action: str = "*", order: int = 100) -> Callable:
    """注册任意 OneBot API 调用后钩子。

    Args:
        action: 匹配的 API 动作，支持 ``"*"`` 或 ``send_*`` 通配。
        order: 同一模块内多个钩子的执行顺序，越小越先执行。
    """

    def decorator(fn):
        metas = getattr(fn, "__api_hook_meta__", [])
        metas.append({"action": action, "order": order})
        setattr(fn, "__api_hook_meta__", metas)
        return fn

    return decorator


def bot_lifecycle_hook(state: str = "*", order: int = 100) -> Callable:
    """注册 Bot 生命周期钩子。

    Args:
        state: ``"login"`` / ``"connected"`` / ``"disconnected"`` / ``"error"`` / ``"*"``。
        order: 同一模块内多个钩子的执行顺序，越小越先执行。
    """

    def decorator(fn):
        metas = getattr(fn, "__lifecycle_hook_meta__", [])
        metas.append({"state": state, "order": order})
        setattr(fn, "__lifecycle_hook_meta__", metas)
        return fn

    return decorator


def event_completed_hook(order: int = 100) -> Callable:
    """注册入站事件处理完成钩子。

    Args:
        order: 同一模块内多个钩子的执行顺序，越小越先执行。
    """

    def decorator(fn):
        metas = getattr(fn, "__event_completed_hook_meta__", [])
        metas.append({"order": order})
        setattr(fn, "__event_completed_hook_meta__", metas)
        return fn

    return decorator


def tool_call_hook(event_type: str = "*", order: int = 100) -> Callable:
    """注册 LLM 工具调用后钩子。

    Args:
        event_type: 只对指定事件类型生效，``"group"`` / ``"private"`` / ``"*"``。
        order: 同一模块内多个钩子的执行顺序，越小越先执行。
    """

    def decorator(fn):
        metas = getattr(fn, "__tool_call_hook_meta__", [])
        metas.append({"event_type": event_type, "order": order})
        setattr(fn, "__tool_call_hook_meta__", metas)
        return fn

    return decorator


# ---------- 简化通用注册表基类 ----------


class _HookRegistry:
    """极简注册表基类：按 bot_id 隔离，支持模块卸载注销。"""

    def __init__(self, log=None) -> None:
        self._hooks: list[dict] = []
        self.log = log or logger

    def unregister_module(self, module: Any) -> None:
        self._hooks = [h for h in self._hooks if h.get("module") is not module]


class SendHookRegistry(_HookRegistry):
    """消息发送成功钩子注册表。

    模块加载时由 ``ModuleRegistry`` 注册（按 bot_id 隔离），
    发送成功后由 ``BotConnection`` 调用 ``run()``。
    """

    def register(
        self,
        *,
        bot_id: int | None,
        module: Any,
        handler: Callable,
        message_type: str = "*",
        order: int = 100,
    ) -> None:
        if handler is None or not callable(handler):
            return
        self._hooks.append({
            "bot_id": bot_id,
            "module": module,
            "handler": handler,
            "message_type": message_type or "*",
            "order": order,
        })

    def match(self, bot_id: int | None, message_type: str) -> list[dict]:
        hooks = [
            h for h in self._hooks
            if h["bot_id"] == bot_id
            and h["message_type"] in ("*", message_type)
        ]
        return sorted(hooks, key=lambda h: h["order"])

    async def run(self, bot: Any, action: str, params: dict | None, response: dict | None) -> None:
        """发送成功后触发匹配的钩子；只有 status=ok 且带 message_id 才触发。"""
        if bot is None or not response or response.get("status") != "ok":
            return
        data = response.get("data") or {}
        if not isinstance(data, dict):
            return
        message_id = data.get("message_id")
        if message_id is None:
            return

        params = params or {}
        message_type = _resolve_send_message_type(action, params)
        ctx = SendContext(
            message_id=message_id,
            bot=bot,
            bot_id=getattr(bot, "bot_id", None),
            action=action,
            params=dict(params),
            response=dict(response),
            message_type=message_type,
            group_id=params.get("group_id"),
            user_id=params.get("user_id"),
        )

        for hook in self.match(ctx.bot_id, message_type):
            try:
                await hook["handler"](ctx)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.log.add_info(f"#{ctx.bot_id}").exception(
                    f"[SendHook] {getattr(hook['module'], 'module_name', '?')} 处理异常: {e}"
                )


class BeforeSendHookRegistry(_HookRegistry):
    """消息发送前钩子注册表。可改写参数或拦截发送。"""

    def register(
        self,
        *,
        bot_id: int | None,
        module: Any,
        handler: Callable,
        message_type: str = "*",
        order: int = 100,
    ) -> None:
        if handler is None or not callable(handler):
            return
        self._hooks.append({
            "bot_id": bot_id,
            "module": module,
            "handler": handler,
            "message_type": message_type or "*",
            "order": order,
        })

    def match(self, bot_id: int | None, message_type: str) -> list[dict]:
        hooks = [
            h for h in self._hooks
            if h["bot_id"] == bot_id
            and h["message_type"] in ("*", message_type)
        ]
        return sorted(hooks, key=lambda h: h["order"])

    async def run(self, bot: Any, action: str, params: dict) -> bool:
        """执行发送前钩子。返回 False 表示应拦截本次发送。"""
        message_type = _resolve_send_message_type(action, params)
        ctx = BeforeSendContext(
            action=action,
            params=dict(params),
            bot=bot,
            bot_id=getattr(bot, "bot_id", None),
            message_type=message_type,
            group_id=params.get("group_id"),
            user_id=params.get("user_id"),
        )
        for hook in self.match(ctx.bot_id, message_type):
            try:
                await hook["handler"](ctx)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.log.add_info(f"#{ctx.bot_id}").exception(
                    f"[BeforeSendHook] {getattr(hook['module'], 'module_name', '?')} 处理异常: {e}"
                )
            if ctx.skip:
                return False
        params.clear()
        params.update(ctx.params)
        return True


class ApiHookRegistry(_HookRegistry):
    """任意 OneBot API 调用后钩子注册表。"""

    def register(
        self,
        *,
        bot_id: int | None,
        module: Any,
        handler: Callable,
        action: str = "*",
        order: int = 100,
    ) -> None:
        if handler is None or not callable(handler):
            return
        self._hooks.append({
            "bot_id": bot_id,
            "module": module,
            "handler": handler,
            "action": action or "*",
            "order": order,
        })

    def match(self, bot_id: int | None, action: str) -> list[dict]:
        hooks = []
        for h in self._hooks:
            if h["bot_id"] != bot_id:
                continue
            pattern = h["action"]
            if pattern == "*" or pattern == action or fnmatch.fnmatch(action, pattern):
                hooks.append(h)
        return sorted(hooks, key=lambda h: h["order"])

    async def run(self, bot: Any, action: str, params: dict, response: dict | None) -> None:
        params = params or {}
        success = bool(response and response.get("status") == "ok")
        data = (response or {}).get("data") or {}
        message_id = data.get("message_id") if isinstance(data, dict) else None
        message_type = _resolve_send_message_type(action, params)
        ctx = ApiContext(
            action=action,
            params=dict(params),
            response=response,
            bot=bot,
            bot_id=getattr(bot, "bot_id", None),
            success=success,
            message_id=message_id,
            message_type=message_type,
            group_id=params.get("group_id"),
            user_id=params.get("user_id"),
        )
        for hook in self.match(ctx.bot_id, action):
            try:
                await hook["handler"](ctx)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.log.add_info(f"#{ctx.bot_id}").exception(
                    f"[ApiHook] {getattr(hook['module'], 'module_name', '?')} 处理异常: {e}"
                )


class LifecycleHookRegistry(_HookRegistry):
    """Bot 生命周期钩子注册表。"""

    def register(
        self,
        *,
        bot_id: int | None,
        module: Any,
        handler: Callable,
        state: str = "*",
        order: int = 100,
    ) -> None:
        if handler is None or not callable(handler):
            return
        self._hooks.append({
            "bot_id": bot_id,
            "module": module,
            "handler": handler,
            "state": state or "*",
            "order": order,
        })

    def match(self, bot_id: int | None, state: str) -> list[dict]:
        hooks = [
            h for h in self._hooks
            if h["bot_id"] == bot_id
            and h["state"] in ("*", state)
        ]
        return sorted(hooks, key=lambda h: h["order"])

    async def run(self, bot: Any, state: str, detail: str = "") -> None:
        if bot is None:
            return
        ctx = LifecycleContext(
            state=state,
            bot=bot,
            bot_id=getattr(bot, "bot_id", None),
            index=getattr(bot, "index", None),
            detail=detail,
        )
        for hook in self.match(ctx.bot_id, state):
            try:
                await hook["handler"](ctx)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.log.add_info(f"#{ctx.bot_id}").exception(
                    f"[LifecycleHook] {getattr(hook['module'], 'module_name', '?')} 处理异常: {e}"
                )


class EventCompletedHookRegistry(_HookRegistry):
    """入站事件处理完成钩子注册表。"""

    def register(
        self,
        *,
        bot_id: int | None,
        module: Any,
        handler: Callable,
        order: int = 100,
    ) -> None:
        if handler is None or not callable(handler):
            return
        self._hooks.append({
            "bot_id": bot_id,
            "module": module,
            "handler": handler,
            "order": order,
        })

    def match(self, bot_id: int | None) -> list[dict]:
        hooks = [h for h in self._hooks if h["bot_id"] == bot_id]
        return sorted(hooks, key=lambda h: h["order"])

    async def run(self, event: Any, state: dict | None = None, duration_ms: float = 0.0) -> None:
        bot = getattr(event, "bot", None)
        if bot is None:
            return
        ctx = EventCompletedContext(
            event=event,
            bot=bot,
            bot_id=getattr(bot, "bot_id", None),
            duration_ms=duration_ms,
            state=dict(state or {}),
        )
        for hook in self.match(ctx.bot_id):
            try:
                await hook["handler"](ctx)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.log.add_info(f"#{ctx.bot_id}").exception(
                    f"[EventCompletedHook] {getattr(hook['module'], 'module_name', '?')} 处理异常: {e}"
                )


def _resolve_send_message_type(action: str, params: dict) -> str:
    """从发送参数推断 group/private；无法推断时返回无类型标记空字符串。"""
    if params.get("message_type") in ("group", "private"):
        return params["message_type"]
    if params.get("group_id"):
        return "group"
    if params.get("user_id"):
        return "private"
    return ""