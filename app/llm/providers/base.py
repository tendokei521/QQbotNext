"""LLM Provider 基类与统一响应实体。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMResponse:
    """统一 LLM 响应（化用 AstrBot LLMResponse 的轻量版）。"""

    text: str = ""
    reasoning: str = ""
    usage: dict = field(default_factory=dict)
    raw: Any = None
    tool_results: list = field(default_factory=list)  # 工具循环执行记录 [{name,args,result}]

    @property
    def ok(self) -> bool:
        return bool(self.text.strip())


@dataclass
class StreamEvent:
    """流式输出事件。

    type:
        - text: 文本增量
        - tool_call: 工具调用碎片（需要按 index 累积）
        - done: 本轮流结束
        - error: 流式请求失败
    """

    type: str = "text"
    text: str = ""
    tool_call: dict | None = None
    finish_reason: str = ""


class BaseProvider:
    """对话 Provider 基类。子类实现 chat()，处理「调哪个 LLM、如何容错」。"""

    name = "base"
    alias_names: tuple[str, ...] = ()

    def __init__(self, config: dict) -> None:
        self.config = config or {}

    async def get_models(self) -> list[str]:
        """返回该连接可用的模型列表；不支持时返回空列表。"""
        return []

    async def chat(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        timeout: int = 30,
    ) -> LLMResponse:
        raise NotImplementedError

    async def chat_stream(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        timeout: int = 30,
        tools: list[dict] | None = None,
        tool_executor=None,
    ):
        """流式对话请求：逐块产出 StreamEvent，支持 tools 的碎片解析。"""
        raise NotImplementedError
        yield StreamEvent(type="error", text="not implemented")  # pragma: no cover
