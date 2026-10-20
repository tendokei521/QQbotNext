"""provider 调用策略（ProviderInvoker）：把「调用约定」也变成可替换的一层。

``register_provider`` 只能换 HTTP 适配器，换不掉框架的调用约定（回退链、空回复重试、
参数怎么传）。迁移到第三方框架（如 AstrBot 的 ToolLoopAgentRunner）时约定往往不同，
所以这里开一个更外层的缝 ``runtime.invoker``。

端到端接线由既有用例反向证明：``tests/test_strip_parentheses.py`` 只打
``app.agent.providers.chat_with_fallback`` 的补丁、``tests/test_llm_initiative_tools.py``
只打 ``app.agent.providers.iter_stream_with_fallback`` 的补丁 —— 这些补丁**只有**在请求
真的经 ``resolve_invoker(...)`` 转发到 providers 层时才生效。
"""

from __future__ import annotations

import pytest

from app.agent.providers.base import LLMResponse
from app.agent.providers.invoker import (
    CHAT_PARAMS,
    STREAM_PARAMS,
    ProviderInvoker,
    resolve_invoker,
)
from app.plugin.features import FeatureRegistry, ProviderInvokerFeatureController


class _Runtime:
    bot_id = 777

    def __init__(self, invoker=None):
        self.invoker = invoker


class _FakeAgentManager:
    def __init__(self, runtime):
        self._runtime = runtime

    def get_runtime(self, bot_id):
        return self._runtime


class _Owner:
    """合格的接管者替身（chat + stream 都在）。"""

    name = "MyInvoker"

    async def chat(self, **kwargs):
        return LLMResponse(text="custom")

    def stream(self, **kwargs):
        raise NotImplementedError


class _OwnerNoStream:
    name = "BadInvoker"

    async def chat(self, **kwargs):
        return LLMResponse(text="custom")


# ==================== 默认实现 ====================


async def test_default_chat_delegates_to_providers(monkeypatch):
    seen = {}

    async def fake_chat(chain, messages, **kwargs):
        seen["chain"] = chain
        seen["messages"] = messages
        seen["kwargs"] = kwargs
        return LLMResponse(text="ok")

    monkeypatch.setattr("app.agent.providers.chat_with_fallback", fake_chat)

    resp = await ProviderInvoker().chat(
        runtime=_Runtime(),
        chain=[{"provider": "openai"}],
        messages=[{"role": "user", "content": "hi"}],
        params={"model": "m", "temperature": 0.3, "tools": None, "unknown_key": "忽略我"},
    )
    assert resp.text == "ok"
    assert seen["chain"] == [{"provider": "openai"}]
    assert seen["kwargs"]["model"] == "m"
    assert seen["kwargs"]["temperature"] == 0.3
    assert "unknown_key" not in seen["kwargs"], "只透传白名单参数，避免把无关参数塞给 provider"


async def test_default_stream_delegates_to_providers(monkeypatch):
    from app.agent.providers.base import StreamEvent

    async def fake_stream(chain, messages, **kwargs):
        yield StreamEvent(type="text", text="第一句")

    monkeypatch.setattr("app.agent.providers.iter_stream_with_fallback", fake_stream)

    events = [
        ev
        async for ev in ProviderInvoker().stream(
            runtime=_Runtime(),
            chain=[{"provider": "openai"}],
            messages=[{"role": "user", "content": "hi"}],
            params={"model": "m", "usage_sink": {}, "unknown_key": 1},
        )
    ]
    assert [e.text for e in events] == ["第一句"]


def test_param_whitelists_are_disjoint_and_documented():
    assert "tools" in CHAT_PARAMS and "max_tool_rounds" in CHAT_PARAMS
    assert "usage_sink" in STREAM_PARAMS and "usage_sink" not in CHAT_PARAMS


# ==================== resolve_invoker ====================


def test_resolve_uses_runtime_invoker():
    custom = _Owner()
    assert resolve_invoker(_Runtime(custom)) is custom


def test_resolve_accepts_duck_typed_invoker():
    custom = _Owner()  # 不继承 ProviderInvoker 也能用
    assert resolve_invoker(_Runtime(custom)) is custom


def test_resolve_falls_back_when_missing_or_broken():
    assert isinstance(resolve_invoker(_Runtime()), ProviderInvoker), "没有 invoker → 默认"
    assert isinstance(resolve_invoker(_Runtime(_OwnerNoStream())), ProviderInvoker), "缺 stream → 默认"
    assert isinstance(resolve_invoker(_Runtime(object())), ProviderInvoker), "不合格对象 → 默认"


# ==================== 声明式接管 ====================


class _RuntimeStub:
    def __init__(self):
        self.invoker = ProviderInvoker()


def _registry(runtime):
    reg = FeatureRegistry()
    reg.register(ProviderInvokerFeatureController(_FakeAgentManager(runtime)))
    return reg


def test_invoker_feature_swaps_and_restores():
    runtime = _RuntimeStub()
    original = runtime.invoker
    reg = _registry(runtime)
    owner = _Owner()

    reg.suppress("provider_invoker", owner, 777)
    assert runtime.invoker is owner

    reg.release("provider_invoker", owner, 777)
    assert runtime.invoker is original, "释放后应还原接管前的调用策略"


def test_invoker_feature_rejects_incomplete_owner():
    runtime = _RuntimeStub()
    original = runtime.invoker
    reg = _registry(runtime)
    bad = _OwnerNoStream()

    reg.suppress("provider_invoker", bad, 777)
    assert runtime.invoker is original, "缺 stream() 的接管者不该被装上"


def test_invoker_feature_handover_between_owners():
    runtime = _RuntimeStub()
    original = runtime.invoker
    reg = _registry(runtime)
    first, second = _Owner(), _Owner()

    reg.suppress("provider_invoker", first, 777)
    reg.suppress("provider_invoker", second, 777)
    assert runtime.invoker is first, "先到先得"

    reg.release("provider_invoker", first, 777)
    assert runtime.invoker is second, "先到者退出后交棒"

    reg.release("provider_invoker", second, 777)
    assert runtime.invoker is original


def test_invoker_feature_status_reports_current():
    runtime = _RuntimeStub()
    reg = _registry(runtime)
    owner = _Owner()
    assert reg.query("provider_invoker", 777)["invoker"] == "ProviderInvoker"
    reg.suppress("provider_invoker", owner, 777)
    assert reg.query("provider_invoker", 777)["invoker"] == "_Owner"


@pytest.mark.parametrize("method", ["chat", "stream"])
def test_default_invoker_exposes_both_methods(method):
    assert callable(getattr(ProviderInvoker(), method))
