"""Q7 中间件 waterfall：插件在**具体一步**插入，而不是整体接管。

方案见 ``docs/llm-context-log-plan.md`` §Q7。此前插件想"插一脚"只有两条路：

- 整体替换（``runtime.assembler`` / ``supersedes=("assembly",)`` / 换 executor）——
  为了改一个字段接管一整层，别人的改动全部失效；
- 打猴补丁改 ``tool_loop`` / ``assembler`` 的内部函数——升级即碎。

本模块给出第三种：**可读可替换的三个钩点**，串行成一条 waterfall。

## 三个钩点

| 钩点 | 时机 | payload | 典型用途 |
|---|---|---|---|
| ``tool_call.before`` | 工具**派发前** | ``{name, args, call_id}`` | 审计、脱敏、拦截（越权/危险参数） |
| ``tool_result.after`` | 工具结果**回来后** | ``{name, call_id, result_text, is_error, code}`` | 统一裁剪、打码、改写回执 |
| ``messages.before_send`` | 最终 messages **发出前** | ``{messages: [...]}`` | 注入一段上下文（对应 `apply_messages_before_send`） |

## 规则

1. 按 ``order`` **升序串行**（同 ``order`` 按注册顺序：``list.sort`` 是稳定排序）；
2. handler 返回 ``dict`` → **替换** payload（只覆盖它给出的键，其余保留）；
   返回 ``None`` → 本步不改（handler 拿到的是一份**浅拷贝**，原地改不会生效，
   必须"返回新 dict"才能生效——语义显式，避免"悄悄改了但没声明"）；
3. handler 返回 ``{"abort": "原因"}`` → 本次运行返回 ``None``（调用方据此失败/拒绝）；
4. handler 抛异常：``fail_closed=True`` → 本次运行返回 ``None``；
   ``fail_closed=False``（默认）→ **只留痕**、跳过该 handler、继续 waterfall；
5. **没有中间件时零开销、零行为变化**：``run_point`` 不拷贝、不构造 handler 参数，
   调用点用 ``has_middleware()`` 先短路（直接返回原值）。

``asyncio.CancelledError`` 永远向上抛（取消不是"中间件失败"）。

## 注册表

挂在 ``runtime.middlewares``（``MiddlewareRegistry``，轻量对象）。注册**幂等**：
同一 ``(point, source, handler)`` 重复注册只保留一条。``unregister_middleware``
按 ``source`` / ``point`` 过滤删除（也接受 ``runtime.middlewares`` 就是个普通 list
的旧式实现——读取侧兼容）。

## 边界

- 只做"一步之内的改写"，**不能**决定要不要发起请求（那是流水线 ``post_assemble``
  钩子的事）、也不认识裸 provider；
- 不新增配置键：谁注册谁负责自己的开关（在模块 ``on_load`` 里判断）。
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.core.logger import account_label, logger

#: 工具派发前（可改 ``{name, args, call_id}``）
POINT_TOOL_CALL_BEFORE = "tool_call.before"
#: 工具结果返回后（可改 ``{name, call_id, result_text, is_error, code}``）
POINT_TOOL_RESULT_AFTER = "tool_result.after"
#: 最终 messages 发出前（可改 ``{messages: [...]}``）
POINT_MESSAGES_BEFORE_SEND = "messages.before_send"

#: 全部合法钩点（注册未知钩点会被拒绝并留痕：拼错不该静默失效）
POINTS: tuple[str, ...] = (
    POINT_TOOL_CALL_BEFORE,
    POINT_TOOL_RESULT_AFTER,
    POINT_MESSAGES_BEFORE_SEND,
)


@dataclass
class Middleware:
    """一条中间件。

    Attributes:
        point: 钩点（``POINTS`` 之一）。
        handler: ``async (payload: dict, *, runtime, ctx) -> dict | None``。
            同步函数也可以（返回值是 awaitable 就 await）。
        order: 升序执行（默认 100；数字小的先跑）。
        fail_closed: ``True`` = handler 异常即中断本步；``False`` = 只留痕并继续。
        source: 声明者（日志用；``unregister_middleware`` 按它卸载）。
    """

    point: str
    handler: Callable[..., Any]
    order: int = 100
    fail_closed: bool = False
    source: str = ""


class MiddlewareRegistry:
    """一个运行时的中间件表（轻量：一个 list + 稳定排序）。"""

    def __init__(self, items: list[Middleware] | None = None) -> None:
        self._items: list[Middleware] = [m for m in (items or []) if isinstance(m, Middleware)]

    def add(self, mw: Middleware) -> bool:
        """加入一条；同一 ``(point, source, handler)`` 重复加入时返回 ``False``（幂等）。"""
        if not isinstance(mw, Middleware):
            raise TypeError(f"middleware 必须是 Middleware，收到 {type(mw).__name__}")
        for existing in self._items:
            if (
                existing.point == mw.point
                and existing.source == mw.source
                and existing.handler is mw.handler
            ):
                return False
        self._items.append(mw)
        return True

    def remove(self, *, source: str = "", point: str | None = None) -> int:
        """按 ``source`` / ``point`` 过滤删除，返回删除条数（两个都不给则一条不动）。"""
        if not source and point is None:
            return 0
        kept: list[Middleware] = []
        removed = 0
        for mw in self._items:
            if (not source or mw.source == source) and (point is None or mw.point == point):
                removed += 1
                continue
            kept.append(mw)
        self._items = kept
        return removed

    def get(self, point: str) -> list[Middleware]:
        """取某钩点的中间件，按 ``order`` 升序（同 order 保持注册顺序）。"""
        items = [mw for mw in self._items if mw.point == point]
        items.sort(key=lambda mw: mw.order)
        return items

    def clear(self) -> int:
        count = len(self._items)
        self._items = []
        return count

    def all(self) -> list[Middleware]:
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)


# ==================== 注册 / 卸载 ====================


def _registry_for(runtime: Any) -> MiddlewareRegistry:
    """取（必要时新建并挂上）``runtime.middlewares``。"""
    registry = getattr(runtime, "middlewares", None)
    if isinstance(registry, MiddlewareRegistry):
        return registry
    if isinstance(registry, (list, tuple)):
        # 兼容"注册表就是个 list"的写法：换成轻量对象并保留已有条目
        registry = MiddlewareRegistry(list(registry))
    else:
        registry = MiddlewareRegistry()
    try:
        runtime.middlewares = registry
    except Exception as e:  # noqa: BLE001 —— 只读/带 __slots__ 的替身：本次注册不生效（不炸调用方）
        logger.debug(f"[Middleware] 无法在 runtime 上持久化注册表（本次注册不生效）: {e}")
    return registry


def _items_for(runtime: Any, point: str) -> list[Middleware]:
    """只读地取某钩点的中间件（不创建注册表；没有就是空列表）。"""
    registry = getattr(runtime, "middlewares", None)
    getter = getattr(registry, "get", None)
    if callable(getter):
        try:
            return list(getter(point))
        except Exception:  # noqa: BLE001 —— 坏注册表当作"没有中间件"
            return []
    if isinstance(registry, (list, tuple)):
        items = [mw for mw in registry if isinstance(mw, Middleware) and mw.point == point]
        items.sort(key=lambda mw: mw.order)
        return items
    return []


def has_middleware(runtime: Any, point: str) -> bool:
    """该钩点有没有中间件（没有时调用方应直接返回原值，零开销）。"""
    return bool(_items_for(runtime, point))


def register_middleware(runtime: Any, mw: Middleware) -> None:
    """注册一条中间件；幂等，未知钩点拒绝并留痕（不抛异常，免得插件加载炸掉）。"""
    if not isinstance(mw, Middleware):
        raise TypeError(f"middleware 必须是 Middleware，收到 {type(mw).__name__}")
    point = str(mw.point or "").strip()
    if point not in POINTS:
        _log(
            runtime,
            "error",
            f"[Middleware] 未知钩点 {point!r}（可用：{', '.join(POINTS)}），来自 {mw.source or '?'}，已忽略",
        )
        return
    if not callable(mw.handler):
        _log(runtime, "error", f"[Middleware] {point} 的 handler 不可调用，来自 {mw.source or '?'}，已忽略")
        return
    mw.point = point
    if not _registry_for(runtime).add(mw):
        _log(runtime, "debug", f"[Middleware] 重复注册已忽略：{point} <- {mw.source or '?'}")
        return
    _log(runtime, "debug", f"[Middleware] 注册 {point} order={mw.order} <- {mw.source or '?'}")


def unregister_middleware(runtime: Any, *, source: str = "", point: str | None = None) -> int:
    """按 ``source`` / ``point`` 卸载，返回卸载条数（两个都不给时不动任何条目）。"""
    if not source and point is None:
        _log(runtime, "warning", "[Middleware] unregister 未给 source/point，已忽略（避免误清空）")
        return 0
    registry = getattr(runtime, "middlewares", None)
    remover = getattr(registry, "remove", None)
    if not callable(remover):
        return 0
    try:
        removed = int(remover(source=source, point=point) or 0)
    except Exception as e:  # noqa: BLE001 —— 卸载失败不该炸调用方
        _log(runtime, "warning", f"[Middleware] 卸载失败（已忽略）: {e}")
        return 0
    _log(
        runtime,
        "debug",
        f"[Middleware] 卸载 {removed} 条（source={source or '*'} point={point or '*'}）",
    )
    return removed


# ==================== 执行 ====================


async def run_point(
    runtime: Any,
    point: str,
    payload: dict,
    *,
    ctx: Any = None,
) -> dict | None:
    """串行跑某钩点的全部中间件，返回最终 payload；``None`` = 被拦截（本步应失败/拒绝）。

    - 没有中间件时**原样返回** ``payload``（不拷贝、不遍历，零开销）；
    - handler 返回 dict → 合并进 payload（但它拿到的是浅拷贝：原地改不生效）；
    - 返回 ``{"abort": "原因"}`` → 立刻返回 ``None``；
    - 抛异常：``fail_closed=True`` → 返回 ``None``；否则留痕后继续。

    Args:
        runtime: AgentRuntime（注册表与日志用；可为 None —— 那就没有中间件）。
        point: 钩点名。
        payload: 这一步的输入（形状见模块 docstring）。
        ctx: 本轮上下文（``LlmContext`` 或 ``ToolContext``，透传给 handler）。
    """
    items = _items_for(runtime, point)
    if not items:
        return payload

    current: dict = dict(payload or {})
    for mw in items:
        try:
            out = mw.handler(dict(current), runtime=runtime, ctx=ctx)
            if inspect.isawaitable(out):
                out = await out
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 —— 失败策略由中间件自己声明
            if mw.fail_closed:
                _log(
                    runtime,
                    "warning",
                    f"[Middleware] {point} <- {mw.source or '?'} 异常且 fail_closed=True，本步中断: {e}",
                )
                return None
            _log(
                runtime,
                "warning",
                f"[Middleware] {point} <- {mw.source or '?'} 异常（fail_open，已跳过）: {e}",
            )
            continue

        if out is None:
            continue
        if not isinstance(out, dict):
            _log(
                runtime,
                "warning",
                f"[Middleware] {point} <- {mw.source or '?'} 返回了非 dict（{type(out).__name__}），已忽略",
            )
            continue
        reason = str(out.get("abort") or "").strip()
        if reason:
            _log(
                runtime,
                "warning",
                f"[Middleware] {point} <- {mw.source or '?'} 拦截本步：{reason}",
            )
            return None
        current.update(out)
    return current


async def apply_messages_before_send(
    runtime: Any,
    messages: list[dict],
    *,
    ctx: Any = None,
) -> list[dict] | None:
    """``messages.before_send`` 的调用侧封装：返回改写后的 messages，``None`` = 本步拦截。

    没有中间件时**原样返回同一个 list**（零开销）；handler 只改写 ``{"messages": …}``。
    """
    point = POINT_MESSAGES_BEFORE_SEND
    if not has_middleware(runtime, point):
        return messages
    out = await run_point(runtime, point, {"messages": list(messages or [])}, ctx=ctx)
    if out is None:
        return None
    value = out.get("messages")
    if not isinstance(value, list):
        _log(runtime, "warning", f"[Middleware] {point} 未给出 messages 列表，已忽略改写")
        return messages
    return value


# ==================== 日志 ====================


def _log(runtime: Any, level: str, message: str) -> None:
    """按 bot 记一条中间件日志（level 为 debug/info/warning/error）。"""
    try:
        prefix = logger.add_info(account_label(getattr(runtime, "bot_id", None)))
        getattr(prefix, level, prefix.debug)(message)
    except Exception as e:  # noqa: BLE001 —— 日志失败绝不能影响中间件语义
        # 连 bot 前缀都取不到时退回全局 logger（单层兜底，不再嵌套 except）
        logger.debug(f"[Middleware] 记录失败（已忽略）: {e} | 原文: {message}")
