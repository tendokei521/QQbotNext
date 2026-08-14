"""轻量工具调用层（对齐 AstrBot FunctionTool 的简化版）。

一个 ToolSpec = 名称 + 描述 + 参数 JSON Schema + 处理器（async (args) -> str）。
to_openai() 输出 OpenAI 原生 function 定义，交给 Provider 的工具循环执行。
"""

from __future__ import annotations

from typing import Awaitable, Callable

ToolHandler = Callable[[dict], Awaitable[str]]


class ToolSpec:
    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict,
        handler: ToolHandler,
    ) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters
        self.handler = handler

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


def make_executor(specs: list[ToolSpec]) -> ToolHandler:
    """按工具名分发到对应处理器；未知工具返回错误文本（回传给 LLM 自纠错）。"""

    async def _executor(name: str, args: dict) -> str:
        for spec in specs:
            if spec.name == name:
                return await spec.handler(args)
        return f"error: 未知工具 {name}"

    return _executor
