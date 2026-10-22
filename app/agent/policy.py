"""策略配置的**统一取用口径**。

## 背景

v3 把一批"策略键"从 agent 配置搬进了功能模块。框架的机制函数仍需要读这些键，
但读的应当是**模块的配置**，而不是 agent 配置。

约定：模块接管时把一个配置视图挂到运行时的同名槽上：

| 运行时属性 | 由哪个模块设置 | 覆盖哪些键 |
|---|---|---|
| `runtime.output_options` | `llm_output` | 流式 / 发言通道 |
| `runtime.assembly_options` | `llm_assembly` | 组装档位 / 行为规则 / 请求落盘 |
| `runtime.meta_options` | `llm_message_meta` | 消息元信息 / 历史清洗 |
| `runtime.context_options` | `llm_context` | 指代 / 展开 / 预历史 |
| `runtime.memory_options` | `llm_memory` | 长期记忆 |
| `runtime.knowledge_options` | `llm_knowledge` | 知识库 |
| `runtime.tools_options` | `llm_tools_extra` | OneBot / MCP 工具 |
| `runtime.proactive_options` | `llm_proactive` | 主动消息 |
| `runtime.schedule_options` | `llm_schedule` | 定时任务 |

两条取用口径：

- `policy_config(runtime, kind)` —— 只关心**一个模块**的键时用它（定位精准）；
- `policy_view(runtime)` —— 机制会**跨模块**读键时用它（如发言通道同时需要
  `assembly_profile` + `stream_output`），内部按上表顺序链式查询再回退 agent 配置。
"""

from __future__ import annotations

from typing import Any


def _policy_views(runtime: Any) -> list:
    """按固定顺序列出可用的策略视图（先模块、后 agent 配置）。"""
    order = (
        "output_options",
        "assembly_options",
        "meta_options",
        "context_options",
        "memory_options",
        "knowledge_options",
        "tools_options",
        "proactive_options",
        "schedule_options",
    )
    views = []
    for attr in order:
        view = getattr(runtime, attr, None) if runtime is not None else None
        if view is not None:
            views.append(view)
    return views


def policy_view(runtime: Any) -> Any:
    """把所有已接管的策略视图**串成一条链**再回退 agent 配置。

    为什么需要：有些机制会跨模块读键 —— 例如发言通道要同时看
    `assembly_profile`（归 `llm_assembly`）和 `stream_output`/`speech_via_tool`
    （归 `llm_output`）。只给一个视图会让另一半读不到，行为静默退化。

    Returns:
        配置对象（有 ``get``）；``runtime`` 为空时返回 None。
    """
    if runtime is None:
        return None
    return ChainedConfig(runtime)


class ChainedConfig:
    """链式配置：按注册顺序问每个模块视图，全都答不上再问 agent 配置。"""

    def __init__(self, runtime: Any, views: list | None = None) -> None:
        self._runtime = runtime
        self._views = views if views is not None else _policy_views(runtime)

    def get(self, key: str, default: Any = None) -> Any:
        for view in self._views:
            value = _safe_get(view, key, None)
            if value is not None:
                return value
        return _safe_get(getattr(self._runtime, "config", None), key, default)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._runtime.config, name)


def _safe_get(config: Any, key: str, default: Any) -> Any:
    getter = getattr(config, "get", None)
    if not callable(getter):
        return default
    try:
        return getter(key, default)
    except Exception:  # noqa: BLE001
        return default


def policy_config(runtime: Any, kind: str) -> Any:
    """取某类策略的配置对象。

    Args:
        runtime: ``AgentRuntime``（可为 None）。
        kind: 槽名（``assembly`` / ``meta`` / ``context`` / ``memory`` /
            ``knowledge`` / ``tools``）。

    Returns:
        模块提供的配置视图（若已接管），否则 agent 配置对象（可能为 None）。
    """
    view = getattr(runtime, f"{kind}_options", None) if runtime is not None else None
    if view is not None:
        return view
    return getattr(runtime, "config", None)


def policy_get(runtime: Any, kind: str, key: str, default: Any = None) -> Any:
    """``policy_config`` + 安全 ``get`` 的合体（读失败返回默认值）。"""
    config = policy_config(runtime, kind)
    getter = getattr(config, "get", None)
    if not callable(getter):
        return default
    try:
        return getter(key, default)
    except Exception:  # noqa: BLE001 —— 配置读取失败不该中断机制
        return default


class RuntimeFacade:
    """把运行时包装成"带着模块配置的 module 对象"。

    框架里 `ProactiveManager` / `TaskScheduler` 这类实现构造时接收一个"module"，
    从中读 `config` / `bot_id` / `ctx.services.task_manager` / `provider_chain()`。
    本门面把 ``config`` 换成模块配置视图，其余属性透传给真实运行时 ——
    于是业务键彻底离开 agent 配置，而计时/状态机等机制一行不改。
    """

    def __init__(self, runtime: Any, config: Any) -> None:
        self._runtime = runtime
        self.config = config

    def __getattr__(self, name: str) -> Any:
        return getattr(self._runtime, name)

    @property
    def runtime(self) -> Any:
        return self._runtime


def facade_for(runtime: Any, kind: str, module_config: Any, owned_keys: Any) -> RuntimeFacade:
    """构造"带模块配置视图"的运行时门面。"""
    from app.agent.delivery.output import ConfigView

    view = ConfigView(getattr(runtime, "config", None), module_config, owned_keys)
    return RuntimeFacade(runtime, view)


__all__ = [
    "policy_config",
    "policy_get",
    "policy_view",
    "ChainedConfig",
    "RuntimeFacade",
    "facade_for",
]
