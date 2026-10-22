"""上下文块与工具源的**注册表**（可组合接缝，不排他）。

## 与槽的区别

`assembly` / `provider_invoker` / `output` 是**排他槽**（谁接管谁独占）。
上下文与工具是**多方贡献**的：记忆想加一个记忆块、群记录想加一个环境块、
工具扩展想加一批工具 —— 它们应该同时存在，而不是抢一个位置。

所以这里用注册表（R3 可组合）：模块 `register` 自己的贡献者，框架按 `order` 收集。

## 契约

```python
class ContextProvider(Protocol):
    name: str          # 块名（诊断用）
    order: int         # 收集顺序（越小越前）
    owner: Any         # 归属（模块实例；卸载时按它批量移除）
    async def collect(self, scope: TurnScope) -> str | None: ...

class ToolSource(Protocol):
    name: str
    order: int
    owner: Any
    async def collect(self, scope: TurnScope) -> list[ToolSpec]: ...
```

`TurnScope` 是只读的本轮取值结果，避免 provider 反过来依赖 `PromptRequest`（会成环）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.core.logger import logger


@dataclass(frozen=True)
class TurnScope:
    """本轮请求的只读上下文（provider 拿它决定要不要贡献内容）。"""

    runtime: Any = None
    config: Any = None
    session_id: str = ""
    is_private: bool = False
    user_id: str = ""
    group_id: str | None = None
    user_text: str = ""
    #: 已渲染的会话历史（只读快照，供"基于历史"的贡献者使用）
    history: tuple = field(default_factory=tuple)


@runtime_checkable
class ContextProvider(Protocol):
    """贡献一段 system 侧上下文（记忆 / 群记录 / 指代 …）。"""

    name: str
    order: int
    owner: Any

    async def collect(self, scope: TurnScope) -> str | None: ...


@runtime_checkable
class ToolSource(Protocol):
    """贡献一批 LLM 工具（OneBot / MCP / 记忆 / 知识库 …）。"""

    name: str
    order: int
    owner: Any

    async def collect(self, scope: TurnScope) -> list[Any]: ...


# ==================== 注册 / 移除 ====================


def register_context_provider(runtime: Any, provider: Any) -> Any:
    """把一个上下文贡献者登记到运行时（按 name 去重，重复即替换）。"""
    if runtime is None or not callable(getattr(provider, "collect", None)):
        raise ValueError("context provider 必须实现 collect(scope)")
    providers = _bucket(runtime, "context_providers")
    providers[:] = [p for p in providers if getattr(p, "name", None) != getattr(provider, "name", None)]
    providers.append(provider)
    providers.sort(key=lambda p: int(getattr(p, "order", 100)))
    return provider


def register_tool_source(runtime: Any, source: Any) -> Any:
    """把一个工具源登记到运行时（按 name 去重，重复即替换）。"""
    if runtime is None or not callable(getattr(source, "collect", None)):
        raise ValueError("tool source 必须实现 collect(scope)")
    sources = _bucket(runtime, "tool_sources")
    sources[:] = [s for s in sources if getattr(s, "name", None) != getattr(source, "name", None)]
    sources.append(source)
    sources.sort(key=lambda s: int(getattr(s, "order", 100)))
    return source


def unregister_owner(runtime: Any, owner: Any) -> int:
    """移除某归属者登记的全部贡献者（模块卸载时调用），返回移除数量。"""
    if runtime is None:
        return 0
    removed = 0
    for attr in ("context_providers", "tool_sources"):
        bucket = _bucket(runtime, attr)
        keep = [p for p in bucket if getattr(p, "owner", None) is not owner]
        removed += len(bucket) - len(keep)
        bucket[:] = keep
    return removed


def _bucket(runtime: Any, attr: str) -> list:
    bucket = getattr(runtime, attr, None)
    if bucket is None:
        bucket = []
        try:
            setattr(runtime, attr, bucket)
        except Exception as e:  # noqa: BLE001 —— 只读替身（测试）允许失败
            logger.debug(f"[Provider] 无法挂载 {attr}（已忽略）: {e}")
    return bucket


# ==================== 收集 ====================


async def collect_context_blocks(runtime: Any, scope: TurnScope) -> list[tuple[str, str]]:
    """按 order 收集各贡献者的文本块；空块与异常都被忽略（留痕）。"""
    blocks: list[tuple[str, str]] = []
    for provider in list(_bucket(runtime, "context_providers")):
        name = str(getattr(provider, "name", "?") or "?")
        try:
            text = await provider.collect(scope)
        except Exception as e:  # noqa: BLE001 —— 单个贡献者失败不该毁掉整轮
            logger.add_info(f"#{getattr(runtime, 'bot_id', '?')}").debug(
                f"[Provider] {name} 收集失败（已忽略）: {e}"
            )
            continue
        if text and str(text).strip():
            blocks.append((name, str(text)))
    return blocks


async def collect_tool_specs(runtime: Any, scope: TurnScope) -> list[Any]:
    """按 order 收集各工具源的工具定义；异常只留痕。"""
    specs: list[Any] = []
    for source in list(_bucket(runtime, "tool_sources")):
        name = str(getattr(source, "name", "?") or "?")
        try:
            produced = await source.collect(scope)
        except Exception as e:  # noqa: BLE001
            logger.add_info(f"#{getattr(runtime, 'bot_id', '?')}").debug(
                f"[Provider] 工具源 {name} 收集失败（已忽略）: {e}"
            )
            continue
        specs.extend(list(produced or []))
    return specs


# ==================== 便捷基类 ====================


class SimpleContextProvider:
    """函数式贡献者的最小实现（模块可以直接用它注册一个回调）。"""

    def __init__(self, name: str, fn, *, order: int = 100, owner: Any = None) -> None:
        self.name = name
        self.order = order
        self.owner = owner
        self._fn = fn

    async def collect(self, scope: TurnScope) -> str | None:
        result = self._fn(scope)
        if hasattr(result, "__await__"):
            return await result
        return result


class SimpleToolSource:
    """函数式工具源的最小实现。"""

    def __init__(self, name: str, fn, *, order: int = 100, owner: Any = None) -> None:
        self.name = name
        self.order = order
        self.owner = owner
        self._fn = fn

    async def collect(self, scope: TurnScope) -> list[Any]:
        result = self._fn(scope)
        if hasattr(result, "__await__"):
            return await result
        return list(result or [])
