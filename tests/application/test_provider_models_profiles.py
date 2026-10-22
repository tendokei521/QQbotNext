"""Provider 模型实例与全局设置测试。"""

import pytest

from app.agent.providers.base import LLMResponse
from app.agent.providers.runtime_manager import ProviderRuntimeManager
from app.application.provider_model_service import ProviderModelService
from app.application.provider_preset_service import ProviderPresetService


class _FakeModelListProvider:
    """连接测试优先走 /models，不应触发 chat。"""

    def __init__(self, config):
        self.config = config

    async def get_models(self):
        return ["deepseek-chat", "deepseek-reasoner"]

    async def chat(self, *args, **kwargs):
        raise AssertionError("连接测试不应走到 chat")


class _FakeChatFallbackProvider:
    """/models 不可用时回退 chat；只要请求成功返回就算连接正常。"""

    def __init__(self, config):
        self.config = config

    async def get_models(self):
        return []

    async def chat(self, *args, **kwargs):
        return LLMResponse(text="", raw={})


@pytest.fixture
async def preset_service(config_service):
    return ProviderPresetService(config_service)


@pytest.fixture
async def model_service(config_service):
    return ProviderModelService(config_service, ProviderRuntimeManager(config_service))


async def test_provider_model_crud_and_delete_ref(preset_service, model_service, config_service):
    preset = await preset_service.create_preset({
        "name": "模型测试",
        "provider": "openai",
        "config": {"api_base": "https://api.deepseek.com", "api_key": "sk-model"},
    })
    created = await model_service.create_model(preset["id"], {
        "model": "deepseek-chat",
        "temperature": 0.5,
        "max_tokens": 2048,
    })
    assert created["model"] == "deepseek-chat"
    assert created["config"]["temperature"] == 0.5

    listed = model_service.list_models(preset["id"])
    assert len(listed) == 1
    assert listed[0]["model"] == "deepseek-chat"

    updated = await model_service.update_model(created["id"], {"temperature": 0.9})
    assert updated["config"]["temperature"] == 0.9

    await config_service.save_module_config("agent", 200, {"provider_model_id": created["id"]})
    with pytest.raises(ValueError, match="请先解除引用"):
        await model_service.delete_model(created["id"])

    await config_service.save_module_config("agent", 200, {})
    await model_service.delete_model(created["id"])
    assert model_service.get_model(created["id"]) is None


async def test_provider_model_modalities(preset_service, model_service, config_service):
    preset = await preset_service.create_preset({
        "name": "模态测试",
        "provider": "openai",
        "config": {"api_base": "https://api.deepseek.com", "api_key": "sk-model"},
    })

    created = await model_service.create_model(preset["id"], {
        "model": "gpt-4o",
        "provider_type": "chat",
        "modalities": ["text", "image", "tool_use"],
    })
    assert created["config"]["modalities"] == ["text", "image", "tool_use"]

    # 新建时未显式声明，按能力类型给默认值
    defaulted = await model_service.create_model(preset["id"], {
        "model": "voice-model",
        "provider_type": "tts",
    })
    assert defaulted["config"]["modalities"] == ["audio"]

    # 更新时过滤非法值并去重
    updated = await model_service.update_model(created["id"], {
        "modalities": ["text", "image", "bad", "image"],
    })
    assert updated["config"]["modalities"] == ["text", "image"]


async def test_provider_settings_save(model_service):
    settings = await model_service.save_settings({
        "default_preset_id": "p1",
        "default_model_id": "m1",
        "fallback_model_ids": ["m2"],
        "provider_pool": ["*"],
    })
    assert settings["default_preset_id"] == "p1"
    assert settings["fallback_model_ids"] == ["m2"]


async def test_preset_test_prefers_models(monkeypatch, preset_service):
    preset = await preset_service.create_preset({
        "name": "连接测试-模型列表",
        "provider": "openai",
        "config": {"api_base": "https://api.deepseek.com", "api_key": "sk-test"},
    })
    monkeypatch.setattr(
        "app.application.provider_preset_service.get_provider",
        lambda cfg: _FakeModelListProvider(cfg),
    )
    result = await preset_service.test_preset(preset["id"])
    assert result["ok"] is True
    assert result.get("models") == ["deepseek-chat", "deepseek-reasoner"]


async def test_preset_test_fallback_success_on_raw(monkeypatch, preset_service):
    preset = await preset_service.create_preset({
        "name": "连接测试-回退",
        "provider": "openai",
        "config": {"api_base": "https://api.deepseek.com", "api_key": "sk-test"},
    })
    monkeypatch.setattr(
        "app.application.provider_preset_service.get_provider",
        lambda cfg: _FakeChatFallbackProvider(cfg),
    )
    result = await preset_service.test_preset(preset["id"])
    assert result["ok"] is True