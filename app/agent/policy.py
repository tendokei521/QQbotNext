"""策略配置的**统一取用口径**。

## 背景

v3 把一批"策略键"从 agent 配置搬进了功能模块。框架的机制函数仍需要读这些键，
但读的应当是**模块的配置**，而不是 agent 配置。

约定：模块接管时把一个配置视图挂到运行时的同名槽上：

| 运行时属性 | 由哪个模块设置 | 覆盖哪些键 |
|---|---|---|
| `runtime.assembly_options` | `llm_assembly` | 组装档位 / 行为规则 / 请求落盘 |
| `runtime.meta_options` | `llm_message_meta` | 消息元信息 / 历史清洗 |
| `runtime.context_options` | `llm_context` | 指代 / 展开 / 预历史 |
| `runtime.memory_options` | `llm_memory` | 长期记忆 |
| `runtime.knowledge_options` | `llm_knowledge` | 知识库 |
| `runtime.tools_options` | `llm_tools_extra` | OneBot / MCP 工具 |

`policy_config(runtime, kind)` 就是读它的唯一入口：
**有视图用视图，没有就回退 agent 配置**（迁移期行为不变）。
"""

from __future__ import annotations

from typing import Any


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


__all__ = ["policy_config", "policy_get", "RuntimeFacade", "facade_for"]
