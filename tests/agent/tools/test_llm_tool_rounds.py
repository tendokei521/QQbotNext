"""工具循环轮数（max_tool_rounds）统一与下传的回归测试。

历史问题：非流式走 Provider 默认值、流式在 ``chat.stream_response`` 里硬编码 ``range(5)``，
两处不一致且用户不可配。而“展开缺失的聊天环境 → 再判断是否还缺 → 再展开”本身是多段链条，
轮数被硬切就表现为模型“半途而废”直接作答。

同时锁住一个静默降级风险：第三方通过 ``register_provider`` 注册的适配器可能没有
``max_tool_rounds`` 形参，若直接传会抛 TypeError，被 ``chat_with_fallback`` 的异常兜底
吞掉后会**静默换下一个 Provider**（表象是“模型没反应”，排查成本极高）。
"""

from __future__ import annotations

from app.agent.providers import PROVIDERS, _accepts_kwarg, chat_with_fallback
from app.agent.providers.base import BaseProvider, LLMResponse

# ---------- 参数下传 ----------


# ---------- 参数下传 ----------


async def test_rounds_are_forwarded_to_provider(monkeypatch):
    seen: dict = {}

    class _Provider(BaseProvider):
        async def chat(self, messages, *, model=None, max_tool_rounds: int = 5, **_kw):
            seen["rounds"] = max_tool_rounds
            return LLMResponse(text="ok")

    monkeypatch.setitem(PROVIDERS, "rounds_probe", _Provider)
    resp = await chat_with_fallback(
        [{"provider": "rounds_probe", "model": "m"}],
        [{"role": "user", "content": "hi"}],
        max_tool_rounds=11,
    )

    assert resp.text == "ok"
    assert seen["rounds"] == 11


async def test_provider_without_the_kwarg_is_still_called(monkeypatch):
    """旧适配器（无该形参）不能被 TypeError 静默跳过。"""
    seen: dict = {}

    class _LegacyProvider(BaseProvider):
        async def chat(self, messages, *, model=None, temperature=0.7, max_tokens=1024, timeout=30,
                       tools=None, tool_executor=None):
            seen["called"] = True
            return LLMResponse(text="legacy-ok")

    monkeypatch.setitem(PROVIDERS, "legacy_probe", _LegacyProvider)
    resp = await chat_with_fallback(
        [{"provider": "legacy_probe"}],
        [{"role": "user", "content": "hi"}],
        max_tool_rounds=7,
    )

    assert seen.get("called") is True
    assert resp.text == "legacy-ok"


async def test_base_provider_declares_the_kwarg():
    """基类显式声明 tools/tool_executor/max_tool_rounds，避免调用方逐家内省。"""
    import inspect

    params = inspect.signature(BaseProvider.chat).parameters
    for name in ("tools", "tool_executor", "max_tool_rounds"):
        assert name in params


async def test_rounds_passed_to_native_providers():
    """三家原生适配器都接受该参数（否则会退回内省分支而丢失配置）。"""
    from app.agent.providers.anthropic import AnthropicProvider
    from app.agent.providers.gemini import GeminiProvider
    from app.agent.providers.openai_compat import OpenAICompatProvider

    for cls in (OpenAICompatProvider, AnthropicProvider, GeminiProvider):
        assert _accepts_kwarg(cls.chat, "max_tool_rounds") is True
