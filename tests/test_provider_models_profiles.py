"""Provider 模型实例 / 全局设置 / 配置档案路由测试。"""

import pytest

from app.infrastructure.config.config_service import ConfigService
from app.llm.providers.base import LLMResponse
from app.llm.providers.runtime_manager import ProviderRuntimeManager
from app.services.config_profile_service import ConfigProfileService
from app.services.provider_model_service import ProviderModelService
from app.services.provider_preset_service import ProviderPresetService


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


@pytest.fixture
async def profile_service(config_service):
    return ConfigProfileService(config_service)


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


async def test_provider_settings_save(model_service):
    settings = await model_service.save_settings({
        "default_preset_id": "p1",
        "default_model_id": "m1",
        "fallback_model_ids": ["m2"],
        "provider_pool": ["*"],
    })
    assert settings["default_preset_id"] == "p1"
    assert settings["fallback_model_ids"] == ["m2"]


async def test_config_profile_and_route(profile_service, config_service):
    profile = await profile_service.create_profile("群A配置", {
        "provider_model_id": "m1",
        "system_prompt": "你是群A助手",
    })
    assert profile["config"]["system_prompt"] == "你是群A助手"

    await profile_service.set_route("group_123", profile["id"])
    routes = profile_service.list_routes()
    assert routes.get("group_123") == profile["id"]

    # 读取 profile 通过 ConfigService 可直接被 AgentConfig 合并
    loaded = config_service.get_config_profile(profile["id"])
    assert loaded["config"]["provider_model_id"] == "m1"

    await profile_service.delete_route("group_123")
    assert profile_service.list_routes() == {}

    await profile_service.delete_profile(profile["id"])
    assert profile_service.get_profile(profile["id"]) is None


async def test_preset_test_prefers_models(monkeypatch, preset_service):
    preset = await preset_service.create_preset({
        "name": "连接测试-模型列表",
        "provider": "openai",
        "config": {"api_base": "https://api.deepseek.com", "api_key": "sk-test"},
    })
    monkeypatch.setattr(
        "app.services.provider_preset_service.get_provider",
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
        "app.services.provider_preset_service.get_provider",
        lambda cfg: _FakeChatFallbackProvider(cfg),
    )
    result = await preset_service.test_preset(preset["id"])
    assert result["ok"] is True