"""``max_tokens`` 归一化：三态语义、写入侧校验、生效值与来源可见。

背景：这一层此前有三个洞，合起来让 Agent 页「最大输出 Tokens」变成死配置：

1. 后端 ``create_model`` 无条件写 ``max_tokens=1024``；
2. 前端 ``saveModel`` 无条件送 ``max_tokens: Number(...) || 1024``、
   ``openEditModel`` 用 ``?? 1024`` 回填 —— 即使后端不烘，UI 也每次都把它烘进来；
3. 迁移函数 ``migrate_legacy_models`` 同样烘 1024。

现在模型实例这一层遵循同一套三态：缺省/空串 = 不覆盖（回落到连接预设 → Agent 页 →
内置默认），数值 = 显式覆盖，``None`` = 清除覆盖。写入侧对"必然跑不通"的值直接拒绝。
"""

from __future__ import annotations

import pytest

from app.agent.providers.policy import REASONING_FLOOR
from app.agent.providers.runtime_manager import ProviderRuntimeManager
from app.services.provider_model_service import (
    ProviderModelService,
    normalize_model_config,
    validate_model_config,
)
from app.services.provider_preset_service import ProviderPresetService


@pytest.fixture
async def services(config_service):
    preset_service = ProviderPresetService(config_service)
    model_service = ProviderModelService(config_service, ProviderRuntimeManager(config_service))
    preset = await preset_service.create_preset({
        "name": "归一化测试",
        "provider": "openai",
        "config": {"api_base": "https://api.deepseek.com", "api_key": "sk-norm"},
    })
    return preset_service, model_service, preset


# ── 三态 ───────────────────────────────────────────────────────────────


async def test_omitting_max_tokens_does_not_bake_a_default(services):
    """不填 = 不覆盖。此前无条件写 1024，永久压死 Agent 页。"""
    _, model_service, preset = services
    created = await model_service.create_model(preset["id"], {"model": "gpt-4o"})
    assert "max_tokens" not in created["config"]


async def test_blank_max_tokens_is_treated_as_inherit(services):
    _, model_service, preset = services
    created = await model_service.create_model(preset["id"], {"model": "gpt-4o", "max_tokens": ""})
    assert "max_tokens" not in created["config"]


async def test_explicit_max_tokens_is_stored(services):
    _, model_service, preset = services
    created = await model_service.create_model(preset["id"], {"model": "gpt-4o", "max_tokens": 8192})
    assert created["config"]["max_tokens"] == 8192


async def test_update_can_hand_control_back_to_the_next_layer(services):
    """None / "" / 0 = 清除覆盖，回落到下一层（此前空值会被折成 1024，还不回去）。"""
    _, model_service, preset = services
    created = await model_service.create_model(preset["id"], {"model": "gpt-4o", "max_tokens": 8192})

    for blank in (None, "", 0):
        updated = await model_service.update_model(created["id"], {"max_tokens": blank})
        assert "max_tokens" not in updated["config"], f"{blank!r} 应清除覆盖"
        # 清除后再填回来
        created = await model_service.update_model(created["id"], {"max_tokens": 4096})
        assert created["config"]["max_tokens"] == 4096


async def test_bare_bool_and_window_are_stored_too(services):
    _, model_service, preset = services
    created = await model_service.create_model(preset["id"], {
        "model": "qwen2.5", "context_window": 32768, "reasoning": False,
    })
    assert created["config"]["context_window"] == 32768
    assert created["config"]["reasoning"] is False

    cleared = await model_service.update_model(created["id"], {"reasoning": ""})
    assert "reasoning" not in cleared["config"], "空值应回到「跟随模型名自动判定」"


def test_normalize_treats_missing_keys_as_untouched():
    existing = {"max_tokens": 2048, "context_window": 32000, "temperature": 0.5}
    assert normalize_model_config({"model": "x"}, existing) == existing


def test_normalize_zero_clears():
    existing = {"max_tokens": 2048, "context_window": 32000}
    result = normalize_model_config({"max_tokens": 0, "context_window": 0}, existing)
    assert "max_tokens" not in result
    assert "context_window" not in result


# ── 写入侧校验 ─────────────────────────────────────────────────────────


async def test_reasoning_model_below_floor_is_rejected_with_actionable_message(services):
    """思考模型填 1024 → 必然 0 字符。必须在写入侧就说清，而不是静默抬升。"""
    _, model_service, preset = services
    with pytest.raises(ValueError, match="思考模型"):
        await model_service.create_model(preset["id"], {
            "model": "deepseek-reasoner", "max_tokens": 1024,
        })


async def test_reasoning_model_at_floor_is_accepted(services):
    _, model_service, preset = services
    created = await model_service.create_model(preset["id"], {
        "model": "deepseek-reasoner", "max_tokens": REASONING_FLOOR,
    })
    assert created["config"]["max_tokens"] == REASONING_FLOOR


async def test_explicit_reasoning_false_bypasses_the_floor(services):
    """用户明确声明"这不是思考模型"时，不该按模型名硬套下限。"""
    _, model_service, preset = services
    created = await model_service.create_model(preset["id"], {
        "model": "deepseek-reasoner", "max_tokens": 1024, "reasoning": False,
    })
    assert created["config"]["max_tokens"] == 1024


async def test_max_tokens_larger_than_window_is_rejected(services):
    _, model_service, preset = services
    with pytest.raises(ValueError, match="上下文窗口"):
        await model_service.create_model(preset["id"], {
            "model": "gpt-4o", "context_window": 8192, "max_tokens": 8192,
        })


async def test_negative_values_are_rejected(services):
    _, model_service, preset = services
    with pytest.raises(ValueError):
        await model_service.create_model(preset["id"], {"model": "gpt-4o", "max_tokens": -5})
    with pytest.raises(ValueError):
        await model_service.create_model(preset["id"], {"model": "gpt-4o", "context_window": -1})


def test_validate_is_a_no_op_for_inherit():
    validate_model_config("gpt-4o", {"temperature": 0.7})  # 不抛异常即通过


# ── 生效值可见 ─────────────────────────────────────────────────────────


async def test_list_models_reports_effective_value_and_origin(services):
    """UI 必须能回答"这次真正用的是多少、谁给的"——这正是 8192 之谜的核心。"""
    _, model_service, preset = services
    await model_service.create_model(preset["id"], {"model": "gpt-4o", "max_tokens": 8192})

    listed = model_service.list_models(preset["id"])
    effective = listed[0]["effective"]
    assert effective["max_tokens"] == 8192
    assert effective["max_tokens_origin"] == "provider 模型"
    assert "max_tokens" in effective["overrides"]


async def test_effective_value_falls_back_to_defaults_when_inherited(services):
    _, model_service, preset = services
    await model_service.create_model(preset["id"], {"model": "gpt-4o"})
    listed = model_service.list_models(preset["id"])
    effective = listed[0]["effective"]
    assert effective["max_tokens"] > 0
    assert effective["overrides"] == [] or "max_tokens" not in effective["overrides"]


async def test_effective_flags_reasoning_models(services):
    _, model_service, preset = services
    created = await model_service.create_model(preset["id"], {"model": "deepseek-reasoner"})
    listed = model_service.list_models(preset["id"])
    assert listed[0]["effective"]["reasoning"] is True
    assert listed[0]["effective"]["max_tokens"] >= REASONING_FLOOR
    assert created["config"].get("max_tokens") is None, "自动抬升不该被写回用户配置"


async def test_effective_window_comes_from_model_config(services):
    _, model_service, preset = services
    await model_service.create_model(preset["id"], {"model": "gpt-4o", "context_window": 128000})
    listed = model_service.list_models(preset["id"])
    assert listed[0]["effective"]["context_window"] == 128000


async def test_get_model_also_carries_effective_view(services):
    _, model_service, preset = services
    created = await model_service.create_model(preset["id"], {"model": "gpt-4o", "max_tokens": 4096})
    fetched = model_service.get_model(created["id"])
    assert fetched["effective"]["max_tokens"] == 4096
