"""LLM 流水线 Hook 注册表。

按 ``stage + event_type`` 保存模块注册的钩子，执行时按 ``order`` 排序。
同时提供 LLM 工具调用后钩子（``ToolCallHookRegistry``）。

## 阶段清单（v3）

流水线只广播**事实**，不认识任何业务：

| 阶段 | 时机 | 典型订阅者 |
|---|---|---|
| `pre_request` | 请求前（组装之前） | 防抖/拦截、元信息渲染、指代预取 |
| `post_assemble` | messages 组装完成、发出前 | 改本轮请求体（迁移/复刻第三方） |
| `post_response` | 非流式回复生成后 | 拆分/改写 |
| `pre_send` | 每条消息发送前 | 拦截/改写单条 |
| `post_send` | 每条消息发送后 | 记账 |
| `post_stream` | 流式整体结束 | 收尾 |
| `message_observed` | **用户说话了**（进入 LLM 前） | 主动消息类业务：重置沉默、安排下次 |
| `bot_sent` | **Bot 发出一条消息后** | 主动消息类业务：重置计时 |
| `turn_started` | **一轮真正开始请求模型前**（组装已完成） | 观测：本轮 model/tools 指纹 |
| `step_completed` | **每跑完一个工具轮**（一次请求） | 观测：逐轮耗时/token/工具数（`ctx.state["step"]` 是 `RoundSummary`） |
| `turn_stopping` | **一轮即将收尾**（写历史/遥测之前） | 观测：读 `ctx.state["turn_reason"]` 统计结局 |
| `turn_completed` | **一轮处理结束**（含工具轮/重试） | 记忆蒸馏、统计、审计 |

> 后三个是 v3 新增。此前框架用 `runtime.proactive.on_message()` /
> `_memory_consolidate()` 这类**硬调用**替业务做事 —— 等于流水线知道
> "主动消息"和"长期记忆"的存在。现在改成广播，谁要做事谁订阅。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app.core.logger import logger, account_label


@dataclass
class RegisteredLlmHook:
    stage: str
    event_type: str
    order: int
    handler: Callable
    module: Any = None


class LlmHookRegistry:
    def __init__(self) -> None:
        self._hooks: list[RegisteredLlmHook] = []

    def register(
        self,
        *,
        stage: str,
        event_type: str = "*",
        order: int = 100,
        handler: Callable,
        module: Any = None,
    ) -> None:
        self._hooks.append(
            RegisteredLlmHook(
                stage=stage,
                event_type=event_type,
                order=order,
                handler=handler,
                module=module,
            )
        )

    def unregister_module(self, module: Any) -> None:
        self._hooks = [h for h in self._hooks if h.module is not module]

    def get(self, stage: str, event_type: str) -> list[RegisteredLlmHook]:
        hooks = [
            h
            for h in self._hooks
            if h.stage == stage and (h.event_type == "*" or h.event_type == event_type)
        ]
        return sorted(hooks, key=lambda h: h.order)

    def all(self) -> list[RegisteredLlmHook]:
        return list(self._hooks)


@dataclass
class ToolCallContext:
    """一次 LLM 工具调用后的上下文。"""

    name: str
    args: dict
    result: str
    success: bool
    duration_ms: float
    tool_ctx: Any = None
    module: Any = None
    bot_id: Any = None
    event_type: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class RegisteredToolCallHook:
    event_type: str
    order: int
    handler: Callable
    module: Any = None


class ToolCallHookRegistry:
    """LLM 工具调用后钩子注册表，挂在 AgentRuntime 上。"""

    def __init__(self, log=None) -> None:
        self._hooks: list[RegisteredToolCallHook] = []
        self.log = log or logger

    def register(
        self,
        *,
        event_type: str = "*",
        order: int = 100,
        handler: Callable,
        module: Any = None,
    ) -> None:
        self._hooks.append(
            RegisteredToolCallHook(
                event_type=event_type,
                order=order,
                handler=handler,
                module=module,
            )
        )

    def unregister_module(self, module: Any) -> None:
        self._hooks = [h for h in self._hooks if h.module is not module]

    def match(self, event_type: str) -> list[RegisteredToolCallHook]:
        hooks = [
            h for h in self._hooks
            if h.event_type == "*" or h.event_type == event_type
        ]
        return sorted(hooks, key=lambda h: h.order)

    async def run(self, ctx: ToolCallContext) -> None:
        for hook in self.match(ctx.event_type):
            try:
                await hook.handler(ctx)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.log.add_info(account_label(ctx.bot_id)).exception(
                    f"[ToolCallHook] {getattr(hook.module, 'module_name', '?')} 处理异常: {e}"
                )


async def run_stage(runtime: Any, stage: str, ctx: Any, msg: Any = None) -> bool:
    """执行某阶段的所有 LLM 钩子；返回 ``False`` 表示本轮应中止发送。

    从 ``LlmPipeline._run_stage`` 抽出，供四条组装路径共用（chat 非流式 / chat 流式 /
    主动消息 / 定时任务）——此前只有前两条走流水线，后两条完全不触发钩子。

    对没有事件对象的合成上下文（主动性/定时路径）容错：``ctx.event`` 为 None 时按
    ``"*"`` 匹配钩子，``ctx.job`` 为 None 时跳过 skip/superseded 判定。
    """
    event = getattr(ctx, "event", None)
    event_type = getattr(event, "event_type", "*") or "*"
    registry = getattr(runtime, "llm_hooks", None)
    hooks = registry.get(stage, event_type) if registry is not None else []
    telemetry = getattr(runtime, "telemetry", None)

    for hook in hooks:
        hook_start = time.monotonic()
        try:
            if msg is None:
                await hook.handler(ctx)
            else:
                await hook.handler(ctx, msg)
        except asyncio.CancelledError:
            if telemetry is not None:
                telemetry.record_hook(stage, (time.monotonic() - hook_start) * 1000)
            raise
        except Exception as e:
            logger.add_info(account_label(getattr(runtime, 'bot_id', '?'))).exception(
                f"[LLM Hook] {stage} 处理异常: {e}"
            )
        finally:
            if telemetry is not None:
                telemetry.record_hook(stage, (time.monotonic() - hook_start) * 1000)
        job = getattr(ctx, "job", None)
        if job is not None and (job.skip or job.superseded):
            return False
    return True