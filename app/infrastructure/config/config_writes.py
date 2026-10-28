"""配置写入的并发/事务基础设施（`ConfigService` 写路径的复用件）。

为什么单独成文件：`config_service.py` 有 800 行架构闸门
（`tests/architecture/test_architecture_layout.py`），而下面这些东西是「写路径
一致性保证」这一类关注点，与配置语义无关，拆出来两边都更清楚。

- :class:`ReentrantWriteLock`：写路径会互相调用（``add_bot → save_bots``、
  ``save_channel_override → delete_channel_override``），裸锁会在同一任务里
  二次 acquire 时自锁死；
- :class:`ConfigOp`：`ConfigService.apply_ops` 批量事务的变更描述；
- :data:`OP_KIND_ATTR` / :data:`MISSING`：失败回滚时按 key 记录旧值的辅助；
- :func:`persist_with_retry`：同步写路径后台落盘（重试一次再告警）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class ReentrantWriteLock:
    """按事件循环任务可重入的异步写锁。

    为什么不用裸 `asyncio.Lock`：ConfigService 的写路径会互相调用
    （``add_bot → save_bots``、``delete_bot → save_bots``、
    ``save_channel_override → delete_channel_override``），裸锁会在同一任务里
    二次 acquire 时自锁死。重入按 ``current_task()`` 判断，深度归零才真正释放。

    并发语义：同一进程内所有配置写（账号 / 绑定 / 群覆盖 / 预设 / kv）串行；
    读路径不取锁（读的都是内存快照，写路径保证落库成功后才替换快照）。
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner: asyncio.Task | None = None
        self._depth = 0

    async def __aenter__(self) -> ReentrantWriteLock:
        task = asyncio.current_task()
        if task is not None and task is self._owner:
            self._depth += 1
            return self
        await self._lock.acquire()
        self._owner = task
        self._depth = 1
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._depth -= 1
        if self._depth > 0:
            return
        self._owner = None
        self._lock.release()


@dataclass(frozen=True)
class ConfigOp:
    """一次配置写入操作（供 `ConfigService.apply_ops` 批量事务用）。

    ``kind`` 决定落到哪张表；``value=None`` 表示删除该行。
    key 的形状：
    - ``binding``: ``(capability_id, scope_type, scope_id)``
    - ``channel_override``: ``(account_id, capability, channel_id)``
    - ``role_preset`` / ``provider_preset`` / ``provider_model``: ``(id,)``
    """

    kind: str
    key: tuple
    value: dict | None = None


#: kind → 需要快照/恢复的内存缓存属性名（失败回滚用）
OP_KIND_ATTR = {
    "binding": "_bindings",
    "channel_override": "_channel_overrides",
    "role_preset": "_role_presets",
    "provider_preset": "_provider_presets",
    "provider_model": "_provider_models",
}

#: 「内存里原本没有这个 key」哨兵（回滚时区分"没有"与"值为 None"）
MISSING = object()


def op_memory_slot(store: Any, op: ConfigOp) -> tuple[dict, Any]:
    """返回 op 对应的「内存容器 + key」（apply_ops 失败回滚按这个粒度记录）。"""
    kind = op.kind
    if kind == "binding":
        container = store._bindings.setdefault(str(op.key[0]), {})
        return container, (str(op.key[1]), str(op.key[2]))
    if kind == "channel_override":
        return store._channel_overrides, (str(op.key[0]), str(op.key[1]), str(op.key[2]))
    if kind == "role_preset":
        return store._role_presets, str(op.key[0])
    if kind == "provider_preset":
        return store._provider_presets, str(op.key[0])
    if kind == "provider_model":
        return store._provider_models, str(op.key[0])
    raise ValueError(f"未知的配置操作类型: {kind}")


async def flush_pending_tasks(tasks: set[asyncio.Task], log: Any, timeout: float = 5.0) -> int:
    """等待一组后台落盘任务全部结束，返回已结束数量。

    关停时**必须**在取消任务/关库之前调用：同步写接口
    （`ModuleConfig.set` 等）的最后一次配置修改排在这里，直接取消会让它
    静默丢失（DB 里没有、日志也没有）。超时只告警并取消残留任务，不无限等。
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    flushed = 0
    while True:
        pending = [task for task in tasks if not task.done()]
        if not pending:
            return flushed
        remaining = deadline - loop.time()
        if remaining <= 0:
            log.warning(
                f"[Config] 关停时仍有 {len(pending)} 个落盘任务未完成（超时 {timeout}s），"
                f"取消残留任务: {', '.join(task.get_name() for task in pending)}"
            )
            for task in pending:
                task.cancel()
            return flushed
        done, _ = await asyncio.wait(pending, timeout=remaining)
        flushed += len(done)
        # 任务收尾时可能又排了新的落盘 → 循环到队列清空或总超时


async def persist_with_retry(
    persist_fn: Callable[..., Any],
    args: tuple[Any, ...],
    log: Any,
) -> None:
    """执行一次后台落盘；失败等 0.5s 重试，再失败则 error 留痕。

    同步写接口（`ModuleConfig.set` 等）不能 await，只能"先改内存、后台落盘"。
    后台任务没有调用方可以抛出，所以最终失败只能记日志；但不能静默——否则
    "保存成功"的假象会一直留到重启后发现配置没了。
    """
    async def _final_write() -> None:
        """取消路径的兜底：落盘任务没有调用方可抛，失败也必须留痕。"""
        log.warning("[Config] 落盘任务被取消，尝试完成最后一次写入")
        try:
            await persist_fn(*args)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 —— 兜底写入失败也要留痕
            log.error(f"[Config] 取消时的兜底写入仍失败（本次修改仅内存生效）: {e}")

    for attempt in (1, 2):
        try:
            await persist_fn(*args)
            return
        except asyncio.CancelledError:
            # 关停时任务被取消：仍然尽力写完最后一次再让取消继续传播。
            # `CancelledError` 是 BaseException，下面的 `except Exception` 抓不到。
            await _final_write()
            raise
        except Exception as e:  # noqa: BLE001 —— 后台任务必须留痕，不能静默
            if attempt == 1:
                log.warning(f"[Config] 配置落盘失败，0.5s 后重试: {e}")
                try:
                    await asyncio.sleep(0.5)
                except asyncio.CancelledError:
                    # 取消正好落在重试等待里（旧实现会在这里静默丢数据）
                    await _final_write()
                    raise
            else:
                log.error(f"[Config] 配置落盘失败（已重试，本次修改仅内存生效）: {e}")
