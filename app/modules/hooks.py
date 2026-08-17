"""装饰器风格的钩子声明。

- ``@module_hook``：模块流水线事件钩子（按事件类型注册处理函数）。
- ``@llm_hook``：LLM 流水线阶段钩子（pre_request / post_response / pre_send / post_send）。
- ``@send_hook``：消息发送成功后钩子（按 bot 与消息类型匹配，回调收到 ``SendContext``）。

装饰器本身只把元数据挂到函数对象上，真正的注册由 ``BaseModule.collect_hooks()``
和 ``ModuleRegistry`` 在模块加载时完成。
"""

from __future__ import annotations

import asyncio
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


class SendHookRegistry:
    """消息发送成功钩子注册表。

    模块加载时由 ``ModuleRegistry`` 注册（按 bot_id 隔离），
    发送成功后由 ``BotConnection`` 调用 ``run()``。
    """

    def __init__(self, log=None) -> None:
        self._hooks: list[dict] = []
        self.log = log or logger

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

    def unregister_module(self, module: Any) -> None:
        self._hooks = [h for h in self._hooks if h.get("module") is not module]

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


def _resolve_send_message_type(action: str, params: dict) -> str:
    """从发送参数推断 group/private；无法推断时返回无类型标记空字符串。"""
    if params.get("message_type") in ("group", "private"):
        return params["message_type"]
    if params.get("group_id"):
        return "group"
    if params.get("user_id"):
        return "private"
    return ""