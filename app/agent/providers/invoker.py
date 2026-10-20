"""provider 调用策略（可被插件整体替换）。

``register_provider`` 只能替换**HTTP 适配器**（在框架既定的调用约定内部），
换不掉调用约定本身：多 provider 回退链、空回复重试、参数怎么传、工具循环在哪一层。
迁移到第三方框架（例如 AstrBot 的 ``ToolLoopAgentRunner``）时，这些约定往往不同，
所以这里再开一个更外层的缝：``runtime.invoker``。

默认实现 ``ProviderInvoker`` = 框架既有行为（原样转调 ``chat_with_fallback`` /
``iter_stream_with_fallback``），因此不接管时行为逐字不变。

插件替换方式（任选其一）：

    runtime.invoker = MyInvoker()          # 直接赋值
    supersedes = ("provider_invoker",)     # 声明式接管，卸载自动还原

注意职责边界：``stream()`` 替换的是**单轮流式请求**；流式的多轮工具循环仍在
``chat.stream_response`` 里（要连整条主循环一起接管，用 ``supersedes = ("agent",)``）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

#: ``chat()`` 会透传的参数名（插件实现可只关心其中一部分）
CHAT_PARAMS = (
    "model",
    "temperature",
    "max_tokens",
    "timeout",
    "tools",
    "tool_executor",
    "max_tool_rounds",
    "max_empty_retries",
    "empty_retry_backoff",
    # 整轮的请求次数/累计输入 token 闸门与"这份上下文大概多少 token"的估算
    # （见 request_policy.TurnBudget）：第三方接管调用策略时把它继续往下传即可
    "budget",
    "input_tokens_estimate",
)
#: ``stream()`` 会透传的参数名
STREAM_PARAMS = (
    "model",
    "temperature",
    "max_tokens",
    "timeout",
    "tools",
    "tool_executor",
    "max_empty_retries",
    "empty_retry_backoff",
    "usage_sink",
    "budget",
    "input_tokens_estimate",
)


class ProviderInvoker:
    """默认 provider 调用策略（框架既有行为）。"""

    async def chat(
        self,
        *,
        runtime: Any,
        chain: list[dict],
        messages: list[dict],
        params: dict,
    ):
        """非流式单次请求（含多 provider 回退与空回复重试）。

        用模块属性查找而非模块级 import 绑定：这样 ``monkeypatch`` 换掉
        ``app.agent.providers.chat_with_fallback``（测试与插件热替换的常用手段）能生效。
        """
        from app.agent import providers

        kwargs = {key: params[key] for key in CHAT_PARAMS if key in params}
        return await providers.chat_with_fallback(chain, messages, **kwargs)

    async def stream(
        self,
        *,
        runtime: Any,
        chain: list[dict],
        messages: list[dict],
        params: dict,
    ) -> AsyncIterator[Any]:
        """流式单轮请求（逐个事件产出，含回退与空回复重试）。"""
        from app.agent import providers

        kwargs = {key: params[key] for key in STREAM_PARAMS if key in params}
        async for event in providers.iter_stream_with_fallback(chain, messages, **kwargs):
            yield event


def resolve_invoker(runtime: Any) -> ProviderInvoker:
    """取该运行时的调用策略；缺失或不合格时回退默认实现并留痕。"""
    invoker = getattr(runtime, "invoker", None)
    if isinstance(invoker, ProviderInvoker):
        return invoker
    if invoker is not None and callable(getattr(invoker, "chat", None)) and callable(getattr(invoker, "stream", None)):
        return invoker
    if invoker is not None:
        from app.agent import logger

        logger.add_info(f"#{getattr(runtime, 'bot_id', '?')}").warning(
            f"[LLM] runtime.invoker 不可用（{type(invoker).__name__}），已回退默认调用策略"
        )
    return ProviderInvoker()
