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


class BaseProvider:
    """对话 Provider 基类。子类实现 chat()，处理「调哪个 LLM、如何容错」。"""

    name = "base"

    def __init__(self, config: dict) -> None:
        self.config = config or {}

    async def chat(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 150,
        timeout: int = 30,
    ) -> LLMResponse:
        raise NotImplementedError
