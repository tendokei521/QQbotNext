"""轻量工具调用层：模块工具扩展口 + 上下文 + 执行器。

原有 ToolSpec 只支持 `async (args) -> str`；生产化后：
- 工具处理器统一接收 ``ToolContext``（模块/机器人/会话/事件/运行时/触发者）；
- 模块通过 ``@tool`` 装饰器或 ``TOOLS`` 类属性声明工具；
- 执行器带超时、异常兜底与结果截断，避免把异常/超大结果塞回 LLM 上下文。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.core.logger import logger

# 单次工具执行超时（秒）
TOOL_TIMEOUT = 20
# 工具结果截断长度（防止 pollute 上下文）
TOOL_RESULT_MAX = 2000

ToolHandler = Callable[["ToolContext", dict], Awaitable[str]]


@dataclass
class ToolContext:
    """一次工具调用的运行上下文。

    - ``module``：声明该工具的模块实例
    - ``bot``：当前 Bot（可发消息/查群）
    - ``session_id``：会话 id（group_x / private_y）
    - ``event``：触发事件（主动/定时场景可能为 None）
    - ``runtime``：框架 AgentRuntime
    - ``user_id`` / ``group_id``：触发者 / 触发群
    """

    module: Any = None
    bot: Any = None
    session_id: str = ""
    event: Any = None
    runtime: Any = None
    user_id: Any = None
    group_id: Any = None
    extra: dict = field(default_factory=dict)


class ToolSpec:
    """单个工具定义：名称 + 描述 + 参数 JSON Schema + 处理器。"""

    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict,
        handler: ToolHandler,
        module: Any = None,
    ) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters
        self.handler = handler
        self.module = module

    def to_openai(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def build_tools(specs: list[ToolSpec]) -> list[dict]:
    """ToolSpec 列表 → OpenAI tools 参数。"""
    return [s.to_openai() for s in specs]


def _truncate_result(text: str) -> str:
    text = str(text)
    if len(text) > TOOL_RESULT_MAX:
        return text[:TOOL_RESULT_MAX] + "\n…(结果过长已截断)"
    return text


def make_executor(specs: list[ToolSpec], ctx: ToolContext | None = None) -> ToolHandler:
    """按工具名分发到对应处理器；带超时/异常兜底/结果截断。

    未来扩展：可在此处追加统一审计日志。
    """

    async def _executor(name: str, args: dict) -> str:
        for spec in specs:
            if spec.name != name:
                continue
            try:
                result = await asyncio.wait_for(spec.handler(ctx, args), timeout=TOOL_TIMEOUT)
            except asyncio.TimeoutError:
                result = f"error: 工具 {name} 执行超时（{TOOL_TIMEOUT}s）"
            except Exception as e:
                logger.add_info("Tool").warning(f"[Tool] {name} 执行异常: {e}")
                result = f"error: 工具 {name} 执行异常: {e}"
            return _truncate_result(result)
        return f"error: 未知工具 {name}"

    return _executor


def tool(*, description: str = "", parameters: dict | None = None) -> Callable:
    """装饰器：把一个模块方法声明为 LLM 工具。

    用法：

    .. code-block:: python

        from app.llm import tool

        class Module(BaseModule):
            @tool(description="查询天气", parameters={"type":"object","properties":{"city":{"type":"string"}}})
            async def query_weather(self, ctx: ToolContext, args: dict) -> str:
                return "晴"
    """

    def decorator(fn):
        metas = getattr(fn, "__tool_meta__", [])
        metas.append({
            "description": description,
            "parameters": parameters or {"type": "object", "properties": {}},
        })
        setattr(fn, "__tool_meta__", metas)
        return fn

    return decorator


class ModuleToolRegistry:
    """按 AgentRuntime 保存模块工具；模块加载/卸载时注册/注销。"""

    def __init__(self, log=None) -> None:
        self._specs: list[ToolSpec] = []
        self._by_name: dict[str, ToolSpec] = {}
        self.log = log or logger

    def register_module(self, module) -> int:
        records: list[dict] = []

        cls = type(module)
        for klass in reversed(cls.__mro__):
            for name, attr in vars(klass).items():
                for meta in getattr(attr, "__tool_meta__", []):
                    records.append({"name": name, **meta})

        # 兼容旧式 TOOLS 类属性声明
        for item in getattr(cls, "TOOLS", []) or []:
            if isinstance(item, dict):
                records.append(dict(item))

        made = 0
        for rec in records:
            name = rec.get("name") or rec.get("method") or ""
            method_name = rec.get("handler") or name
            method = getattr(module, method_name, None)
            if not name or method is None or not callable(method):
                continue
            if name in self._by_name:
                self.log.warning(
                    f"[Tool] 工具名冲突 {name}（{module.module_name}）已存在，跳过；请改唯一名称"
                )
                continue
            parameters = rec.get("parameters") or {"type": "object", "properties": {}}
            spec = ToolSpec(
                name=name,
                description=rec.get("description", ""),
                parameters=parameters,
                handler=lambda ctx, args, m=method: m(ctx, args),
                module=module,
            )
            self._specs.append(spec)
            self._by_name[name] = spec
            made += 1
        if made:
            self.log.debug(f"[Tool] 模块 {module.module_name} 注册 {made} 个工具")
        return made

    def unregister_module(self, module) -> int:
        removed = [s for s in self._specs if s.module is module]
        for spec in removed:
            self._by_name.pop(spec.name, None)
        self._specs = [s for s in self._specs if s.module is not module]
        return len(removed)

    def all_specs(self) -> list[ToolSpec]:
        return list(self._specs)

    def enabled_specs(self) -> list[ToolSpec]:
        """只返回「模块启用 + 工具未在 config 中被关闭」的工具。"""
        result = []
        for spec in self._specs:
            module = spec.module
            if module is None:
                result.append(spec)
                continue
            authority = getattr(module, "authority", None)
            if authority is not None and not getattr(authority, "enabled", True):
                continue
            config = getattr(module, "config", None)
            enabled_map = config.get("tools_enabled", {}) if config is not None else {}
            if isinstance(enabled_map, dict) and spec.name in enabled_map and not enabled_map.get(spec.name):
                continue
            result.append(spec)
        return result
