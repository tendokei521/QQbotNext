"""模块用 LLM 客户端（`ctx.services.llm`）：让模块用「系统的 provider」。

原先模块要自己发一次模型请求，只能 `get_provider(dict(module.config.raw_config))`
自备 api_key —— 密钥散落进模块配置、没有回退链、不计记账。这里锁住新门面的行为：

1. 解析顺序：显式 `model_id` → 显式 `preset_id` → **账号自己的模型池**（系统的 provider）；
2. 参数只在显式给出时透传（没给就让 provider 用自己配置里的 temperature/top_p…）；
3. 拿不到配置时返回空 `LLMResponse` 并留痕，**不抛异常**（模块走自己的降级分支）；
4. `resolve()` 只给脱敏摘要（不含 api_key）；
5. 模块侧快捷方式 `module.llm_chat(...)` / `module.llm_stream(...)` 自动带上本账号。
"""

from __future__ import annotations

import pytest

from app.agent.client import ModuleLLMClient
from app.agent.providers.base import LLMResponse, StreamEvent
from app.modules.base import BaseModule, ModuleAuthority, ModuleConfig, ModuleContext, ServiceAccess


class _RuntimeManager:
    def __init__(self, models=None, presets=None):
        self.models = dict(models or {})
        self.presets = dict(presets or {})
        self.calls: list[tuple] = []

    def resolve_provider_config(self, model_id):
        self.calls.append(("model", model_id))
        return self.models.get(str(model_id))

    def resolve_preset_config(self, preset_id):
        self.calls.append(("preset", preset_id))
        return self.presets.get(str(preset_id))


class _ModelService:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def list_models(self, preset_id=None):
        return [dict(r) for r in self.rows]


class _Runtime:
    bot_id = "bot_llm"

    def __init__(self, chain=None):
        self._chain = list(chain or [])

    def provider_chain(self):
        return [dict(c) for c in self._chain]


class _AgentManager:
    def __init__(self, runtime=None):
        self.runtime = runtime

    def get_runtime(self, bot_id):
        return self.runtime


ACCOUNT_CHAIN = [{"provider": "openai", "model": "account-model", "api_key": "sk-account"}]


def _client(*, runtime=None, models=None, presets=None, model_rows=None):
    return ModuleLLMClient(
        agent_manager=_AgentManager(runtime if runtime is not None else _Runtime(ACCOUNT_CHAIN)),
        runtime_manager=_RuntimeManager(models, presets),
        model_service=_ModelService(model_rows),
    )


@pytest.fixture
def captured(monkeypatch):
    """替换 providers 层的调用，记录 chain / messages / kwargs。"""
    seen: dict = {}

    async def fake_chat(chain, messages, **kwargs):
        seen["chain"] = chain
        seen["messages"] = messages
        seen["kwargs"] = kwargs
        return LLMResponse(text="模块拿到了回复", raw={})

    async def fake_stream(chain, messages, **kwargs):
        seen["chain"] = chain
        seen["messages"] = messages
        seen["kwargs"] = kwargs
        yield StreamEvent(type="text", text="流式片段")

    monkeypatch.setattr("app.agent.providers.chat_with_fallback", fake_chat)
    monkeypatch.setattr("app.agent.providers.iter_stream_with_fallback", fake_stream)
    return seen


# ==================== 解析顺序 ====================


async def test_defaults_to_account_model_pool(captured):
    """不传 model_id/preset_id → 用账号自己的模型池（「系统的 provider」）。"""
    client = _client()
    resp = await client.chat([{"role": "user", "content": "hi"}], bot_id="bot_llm")
    assert resp.text == "模块拿到了回复"
    assert captured["chain"] == ACCOUNT_CHAIN


async def test_explicit_model_id_wins(captured):
    model_cfg = {"provider": "anthropic", "model": "claude-x", "api_key": "sk-m"}
    client = _client(models={"m1": model_cfg})
    await client.chat([{"role": "user", "content": "hi"}], bot_id="bot_llm", model_id="m1")
    assert captured["chain"] == [model_cfg], "显式 model_id 优先于账号模型池"


async def test_explicit_preset_id_used_when_no_model(captured):
    preset_cfg = {"provider": "gemini", "api_key": "sk-p"}
    client = _client(presets={"p1": preset_cfg})
    await client.chat([{"role": "user", "content": "hi"}], bot_id="bot_llm", preset_id="p1")
    assert captured["chain"] == [preset_cfg]


async def test_unknown_model_falls_back_to_account_pool(captured):
    """指定了不存在的模型 → 留痕并回退账号模型池，而不是直接失败。"""
    client = _client(models={})
    await client.chat([{"role": "user", "content": "hi"}], bot_id="bot_llm", model_id="missing")
    assert captured["chain"] == ACCOUNT_CHAIN


async def test_no_config_at_all_returns_empty_response(captured):
    client = ModuleLLMClient(agent_manager=_AgentManager(None), runtime_manager=_RuntimeManager())
    resp = await client.chat([{"role": "user", "content": "hi"}], bot_id="bot_llm")
    assert resp.ok is False and resp.text == ""
    assert "chain" not in captured, "没有配置时不该去调 provider"


async def test_bot_id_none_has_no_runtime(captured):
    """不传 bot_id 且没有显式 model_id → 无法定位账号运行时 → 空响应。"""
    client = _client()
    resp = await client.chat([{"role": "user", "content": "hi"}])
    assert resp.ok is False


# ==================== 消息与参数 ====================


async def test_system_is_prepended_once(captured):
    client = _client()
    await client.chat([{"role": "user", "content": "hi"}], bot_id="bot_llm", system="你是翻译官")
    assert captured["messages"][0] == {"role": "system", "content": "你是翻译官"}

    await client.chat(
        [{"role": "system", "content": "已有系统提示"}, {"role": "user", "content": "hi"}],
        bot_id="bot_llm", system="不该重复插入",
    )
    assert captured["messages"][0] == {"role": "system", "content": "已有系统提示"}
    assert len(captured["messages"]) == 2


async def test_only_explicit_params_are_forwarded(captured):
    """没给的参数不覆盖 provider 自己配置的值。"""
    client = _client()
    await client.chat([{"role": "user", "content": "hi"}], bot_id="bot_llm")
    assert captured["kwargs"] == {}

    await client.chat(
        [{"role": "user", "content": "hi"}], bot_id="bot_llm",
        temperature=0.2, max_tokens=64, timeout=15, max_empty_retries=0,
    )
    assert captured["kwargs"] == {
        "temperature": 0.2, "max_tokens": 64, "timeout": 15, "max_empty_retries": 0,
    }


async def test_messages_are_copied_not_mutated(captured):
    client = _client()
    original = [{"role": "user", "content": "hi"}]
    await client.chat(original, bot_id="bot_llm", system="sys")
    assert original == [{"role": "user", "content": "hi"}], "不该改调用方传进来的列表"


async def test_stream_delegates_and_passes_usage_sink(captured):
    client = _client()
    sink: dict = {}
    events = [
        ev async for ev in client.stream(
            [{"role": "user", "content": "hi"}], bot_id="bot_llm", usage_sink=sink
        )
    ]
    assert [e.text for e in events] == ["流式片段"]
    assert captured["kwargs"]["usage_sink"] is sink


async def test_stream_without_config_yields_nothing(captured):
    client = ModuleLLMClient(agent_manager=_AgentManager(None), runtime_manager=_RuntimeManager())
    assert [ev async for ev in client.stream([{"role": "user", "content": "x"}], bot_id="b")] == []


# ==================== 诊断与清单 ====================


async def test_resolve_is_masked(captured):
    """诊断摘要只说明"用了谁"，绝不回吐 api_key。"""
    client = _client(models={"m1": {"provider": "openai", "model": "gpt", "api_key": "sk-secret", "api_base": "http://x"}})
    info = client.resolve(bot_id="bot_llm", model_id="m1")
    assert info["count"] == 1
    row = info["chain"][0]
    assert row["provider"] == "openai" and row["model"] == "gpt" and row["api_base"] == "http://x"
    assert row["has_api_key"] is True
    assert "sk-secret" not in str(info), "不得把密钥写进摘要"


def test_models_filters_by_kind():
    rows = [
        {"id": "c1", "name": "对话模型", "model": "chat-x", "provider_type": "chat"},
        {"id": "e1", "name": "嵌入模型", "model": "embed-x", "provider_type": "embedding"},
    ]
    client = _client(model_rows=rows)
    assert [m["id"] for m in client.models()] == ["c1"]
    assert [m["id"] for m in client.models(kind="embedding")] == ["e1"]
    assert [m["id"] for m in client.models(kind="all")] == ["c1", "e1"]
    assert client.models()[0]["label"] == "对话模型"
    assert client.models()[0]["provider_type"] == "chat"


# ==================== 模块侧快捷方式 ====================


class _Svc:
    def get_module_config(self, module, bot_id):
        return {}

    def set_module_config(self, module, bot_id, data, persist=True):
        return None


class _AuthSvc:
    def get_module_authority(self, module, bot_id):
        return {}

    def set_module_authority(self, module, bot_id, authority):
        return None


class _Mod(BaseModule):
    name = "测试模块"
    sign = "TestMod"


def _module(services):
    config = ModuleConfig("t", "bot_llm", {}, _Svc())
    authority = ModuleAuthority("t", "bot_llm", _AuthSvc())
    ctx = ModuleContext(module_name="t", bot_id="bot_llm", config=config, authority=authority, services=services)
    return _Mod(ctx)


async def test_module_helper_defaults_bot_id(captured):
    """module.llm_chat(...) 自动带上本账号，模块不用自己传 bot_id。"""
    module = _module(ServiceAccess(llm=_client()))
    resp = await module.llm_chat([{"role": "user", "content": "hi"}])
    assert resp.text == "模块拿到了回复"
    assert captured["chain"] == ACCOUNT_CHAIN, "走的是该账号的模型池"


async def test_module_helper_without_client_returns_empty(captured):
    module = _module(ServiceAccess())
    resp = await module.llm_chat([{"role": "user", "content": "hi"}])
    assert resp.ok is False and resp.text == ""
    assert "chain" not in captured


async def test_module_stream_helper(captured):
    module = _module(ServiceAccess(llm=_client()))
    events = [ev async for ev in module.llm_stream([{"role": "user", "content": "hi"}])]
    assert [e.text for e in events] == ["流式片段"]


# ==================== 容器装配 ====================


def test_container_wires_module_llm_and_provider_services(container):
    from app.agent.client import ModuleLLMClient as Client
    from app.services.provider_model_service import ProviderModelService
    from app.services.provider_preset_service import ProviderPresetService
    from app.services.provider_service import ProviderRegistry

    services = container.get(ServiceAccess)
    assert isinstance(services.llm, Client)
    assert isinstance(services.provider_presets, ProviderPresetService)
    assert isinstance(services.provider_models, ProviderModelService)
    assert services.llm is container.get(Client)
    # providers 与 data_sources 是同一个对象：别名只为消除"provider"这个词的歧义
    assert services.data_sources is services.providers
    assert isinstance(services.providers, ProviderRegistry)


async def test_container_llm_uses_real_model_config(container):
    """端到端：注册表里的模型 → 客户端解析出的链（脱敏摘要可核对）。"""
    from app.services.provider_model_service import ProviderModelService
    from app.services.provider_preset_service import ProviderPresetService

    preset = await container.get(ProviderPresetService).create_preset(
        {"name": "模块用预设", "provider": "openai", "config": {"api_base": "http://mod", "api_key": "sk-mod"}}
    )
    model = await container.get(ProviderModelService).create_model(
        preset["id"], {"model": "module-chat", "provider_type": "chat"}
    )

    services = container.get(ServiceAccess)
    info = services.llm.resolve(model_id=model["id"])
    assert info["count"] == 1
    assert info["chain"][0]["model"] == "module-chat"
    assert info["chain"][0]["api_base"] == "http://mod"
    assert info["chain"][0]["has_api_key"] is True


def test_client_tolerates_broken_runtime_manager(monkeypatch):
    """解析器抛异常时只留痕，并回退账号模型池（不把异常抛给模块）。"""
    class _Boom:
        def resolve_provider_config(self, model_id):
            raise RuntimeError("炸了")

    client = ModuleLLMClient(agent_manager=_AgentManager(_Runtime(ACCOUNT_CHAIN)), runtime_manager=_Boom())
    info = client.resolve(bot_id="bot_llm", model_id="m1")
    assert info["count"] == 1, "解析失败后应回退到账号模型池"
    assert info["chain"][0]["model"] == "account-model"
