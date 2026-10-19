"""``max_tokens`` 三层覆盖的真实语义，以及「Agent 页被覆盖」的显式告警。

审查发现的两件事：

1. **Agent 页「最大输出 Tokens」是死配置**：``AgentConfig._base_raw_config()`` 会把
   ``DEFAULT_LLM_CONFIG`` 的每个键铺满（含 ``max_tokens=1024``），随后
   ``_config_for_model_id`` 用预设/模型 config 覆盖；而 ``create_model`` 此前**无条件**
   写 ``max_tokens: data.get(..., 1024) or 1024`` —— 于是每个 UI 建的模型都带一个
   看起来像"用户显式设置"的 1024，运行时永远压过 Agent 层。用户改 Agent 页毫无效果，
   且没有任何提示（线上为修「思考吃满预算」把 Agent 页改成 8192，实际请求仍是 1024）。
2. **溯源失准**：合并后的 cfg 恒带 ``max_tokens``，只看值分辨不出三层，
   于是诊断日志永远写「来自 provider 模型/预设」——连"8192 到底哪来的"都答不出来。

修法：
- ``_config_for_model_id`` 记下**实际生效那一层**（``_max_tokens_origin``）；
- ``create_model``/``update_model`` 只在用户显式填写时落库，显式清空 = 交回 Agent 层；
- Agent 页显式设了却被覆盖时打一条可操作告警（同一组值只提醒一次）。
"""

from __future__ import annotations

import pytest

from app.llm import chat
from app.llm.manager import _origin_of
from app.llm.providers.runtime_manager import ProviderRuntimeManager
from app.services.provider_model_service import ProviderModelService

# ==================== 1. 来源判定（纯函数） ====================


def test_origin_prefers_model_over_preset_and_agent():
    assert _origin_of(
        ("provider 模型", {"max_tokens": 8192}),
        ("连接预设", {"max_tokens": 4096}),
        ("agent 配置", {"max_tokens": 1024}),
    ) == "provider 模型"


def test_origin_falls_back_to_preset():
    assert _origin_of(
        ("provider 模型", {}),
        ("连接预设", {"max_tokens": 4096}),
        ("agent 配置", {"max_tokens": 1024}),
    ) == "连接预设"


def test_origin_falls_back_to_agent():
    assert _origin_of(
        ("provider 模型", {}),
        ("连接预设", {}),
        ("agent 配置", {"max_tokens": 1024}),
    ) == "agent 配置"


def test_origin_ignores_blank_values():
    """空串/None 等于没设置，不能算作来源。"""
    assert _origin_of(
        ("provider 模型", {"max_tokens": ""}),
        ("连接预设", {"max_tokens": None}),
        ("agent 配置", {"max_tokens": 777}),
    ) == "agent 配置"


# ==================== 2. _effective_max_tokens 读来源标记 ====================


def test_effective_max_tokens_reports_model_origin():
    chain = [{"provider_model_id": "m1", "max_tokens": 8192, "_max_tokens_origin": "provider 模型"}]

    assert chat._effective_max_tokens(chain, 1024) == (8192, "provider 模型")


def test_effective_max_tokens_reports_preset_origin():
    chain = [{"max_tokens": 4096, "_max_tokens_origin": "连接预设"}]

    assert chat._effective_max_tokens(chain, 1024) == (4096, "连接预设")


def test_effective_max_tokens_falls_back_to_agent_when_chain_has_none():
    """模型页没填 max_tokens（新语义下就是"交回 Agent 层"）时，Agent 值必须生效。"""
    chain = [{"provider_model_id": "m1", "_max_tokens_origin": "agent 配置"}]

    assert chat._effective_max_tokens(chain, 8192) == (8192, "agent 配置")


# ==================== 3. 被覆盖时的显式告警 ====================


class _FakeConfig:
    def __init__(self, value, explicit=True):
        self._value = value
        self._explicit = explicit

    def has_explicit(self, key):
        return self._explicit and key == "max_tokens"

    def get(self, key, default=None):
        return self._value if key == "max_tokens" else default


class _FakeRuntime:
    def __init__(self, bot_id, config):
        self.bot_id = bot_id
        self.config = config


def test_override_note_warns_when_agent_value_lost():
    chat._MAX_TOKENS_WARNED.clear()
    rt = _FakeRuntime(3301, _FakeConfig(8192))

    note = chat._max_tokens_override_note(rt, 1024, "provider 模型")

    assert "max_tokens=8192 未生效" in note
    assert "1024" in note
    assert "provider 模型" in note


def test_override_note_is_silent_when_agent_value_wins():
    chat._MAX_TOKENS_WARNED.clear()
    rt = _FakeRuntime(3302, _FakeConfig(8192))

    assert chat._max_tokens_override_note(rt, 8192, "agent 配置") == ""


def test_override_note_is_silent_when_value_is_only_a_default():
    """用户没显式设过 → 只是默认值差异，提示就是噪音。"""
    chat._MAX_TOKENS_WARNED.clear()
    rt = _FakeRuntime(3303, _FakeConfig(1024, explicit=False))

    assert chat._max_tokens_override_note(rt, 8192, "provider 模型") == ""


def test_override_note_is_deduplicated():
    """同一条误配置不要每条消息都刷一遍。"""
    chat._MAX_TOKENS_WARNED.clear()
    rt = _FakeRuntime(3304, _FakeConfig(8192))

    first = chat._max_tokens_override_note(rt, 1024, "provider 模型")
    second = chat._max_tokens_override_note(rt, 1024, "provider 模型")

    assert first
    assert second == ""


# ==================== 4. 模型页：不填 = 交回 Agent 层 ====================


@pytest.fixture
async def services(config_service):
    presets = ProviderModelService(config_service, ProviderRuntimeManager(config_service))
    from app.services.provider_preset_service import ProviderPresetService

    return ProviderPresetService(config_service), presets


async def test_create_model_without_max_tokens_leaves_it_unset(services):
    """不填就不落库：否则那个 1024 会永久压住 Agent 页的设置。"""
    preset_service, model_service = services
    preset = await preset_service.create_preset({
        "name": "无 mt",
        "provider": "openai",
        "config": {"api_base": "https://api.deepseek.com", "api_key": "sk-x"},
    })

    created = await model_service.create_model(preset["id"], {"model": "m-a"})

    assert "max_tokens" not in created["config"]


async def test_create_model_with_max_tokens_persists_it(services):
    preset_service, model_service = services
    preset = await preset_service.create_preset({
        "name": "有 mt",
        "provider": "openai",
        "config": {"api_base": "https://api.deepseek.com", "api_key": "sk-x"},
    })

    created = await model_service.create_model(preset["id"], {"model": "m-b", "max_tokens": 8192})

    assert created["config"]["max_tokens"] == 8192


async def test_update_model_can_clear_max_tokens(services):
    """显式清空 = 把控制权交回 Agent 页（此前空值被折成 1024，永远还不回去）。"""
    preset_service, model_service = services
    preset = await preset_service.create_preset({
        "name": "清空 mt",
        "provider": "openai",
        "config": {"api_base": "https://api.deepseek.com", "api_key": "sk-x"},
    })
    created = await model_service.create_model(
        preset["id"], {"model": "m-c", "max_tokens": 8192}
    )

    updated = await model_service.update_model(created["id"], {"max_tokens": ""})

    assert "max_tokens" not in updated["config"]


async def test_update_model_keeps_max_tokens_when_field_absent(services):
    preset_service, model_service = services
    preset = await preset_service.create_preset({
        "name": "保留 mt",
        "provider": "openai",
        "config": {"api_base": "https://api.deepseek.com", "api_key": "sk-x"},
    })
    created = await model_service.create_model(
        preset["id"], {"model": "m-d", "max_tokens": 4096}
    )

    updated = await model_service.update_model(created["id"], {"enabled": False})

    assert updated["config"]["max_tokens"] == 4096
