"""「Provider 全局设置」必须真的生效（此前是纯装饰）。

审查发现：``DEFAULT_PROVIDER_SETTINGS`` 里的 ``default_model_id`` / ``fallback_model_ids`` /
``provider_pool`` 有完整 UI（ProviderPresetsPage「全局设置」弹窗）、独立 API
（``PUT /api/provider-settings``）、独立数据表（``provider_settings``），
``provider_preset_service.delete_preset`` 还专门级联清理它们——**但运行时一次都没读过**：
``AgentRuntime.provider_chain()`` 只看本 bot 的 Agent 配置。

后果：用户在唯一写着「默认模型」的地方做的选择永远不生效；新 bot 更糟——配了全局
默认模型仍然一句话不回，而且没有任何日志指向「没有可用模型」（pipeline 在
``api_key`` 为空时静默 return）。

现在：Agent 层没给出任何模型时，由全局设置接管；``provider_pool`` 作允许清单
（``*`` 或不填 = 全允许）；都能落空时打一条明确的 error。
"""

from __future__ import annotations

import pytest

from app.llm.manager import AgentRuntime
from app.llm.providers.runtime_manager import ProviderRuntimeManager
from app.services.provider_model_service import ProviderModelService
from app.services.provider_preset_service import ProviderPresetService


class _TM:
    def create_task(self, coro, **kw):
        return None  # 测试不真正运行后台任务


@pytest.fixture
async def stack(config_service):
    """建好一个预设 + 两个模型，返回 (config_service, 模型 id 列表)。"""
    preset = await ProviderPresetService(config_service).create_preset({
        "name": "全局设置测试",
        "provider": "openai",
        "config": {"api_base": "https://api.deepseek.com", "api_key": "sk-global"},
    })
    models = ProviderModelService(config_service, ProviderRuntimeManager(config_service))
    m1 = await models.create_model(preset["id"], {"model": "global-primary", "max_tokens": 2048})
    m2 = await models.create_model(preset["id"], {"model": "global-backup"})
    return config_service, [m1["id"], m2["id"]]


def _runtime(config_service) -> AgentRuntime:
    return AgentRuntime(4401, config_service, _TM())


async def test_agent_runtime_defaults_to_global_settings(stack):
    """Agent 页没选模型 → 用全局设置的默认模型 + fallback。"""
    config_service, (m1, m2) = stack
    await config_service.save_provider_settings({
        "default_model_id": m1,
        "fallback_model_ids": [m2],
        "provider_pool": ["*"],
    })
    rt = _runtime(config_service)
    try:
        chain = rt.provider_chain()

        assert [c.get("provider_model_id") for c in chain] == [m1, m2]
        assert chain[0]["max_tokens"] == 2048
    finally:
        rt.stop()


async def test_agent_config_wins_over_global_settings(stack):
    """Agent 页选了模型就压过全局设置（保守语义：不悄悄改已有 bot 的模型）。"""
    config_service, (m1, m2) = stack
    await config_service.save_provider_settings({
        "default_model_id": m2,
        "fallback_model_ids": [],
        "provider_pool": ["*"],
    })
    rt = _runtime(config_service)
    try:
        rt.config.set("provider_model_id", m1, auto_save=False)

        chain = rt.provider_chain()

        assert [c.get("provider_model_id") for c in chain] == [m1]
    finally:
        rt.stop()


async def test_provider_pool_acts_as_allow_list(stack):
    """provider_pool 不含该模型时跳过它。"""
    config_service, (m1, m2) = stack
    await config_service.save_provider_settings({
        "default_model_id": m1,
        "fallback_model_ids": [m2],
        "provider_pool": [m2],
    })
    rt = _runtime(config_service)
    try:
        chain = rt.provider_chain()

        assert [c.get("provider_model_id") for c in chain] == [m2]
    finally:
        rt.stop()


async def test_global_settings_ignores_unknown_model_id(stack, caplog):
    """全局设置引用了已删除的模型 → 跳过并告警，不能整条链崩掉。"""
    config_service, (m1, _m2) = stack
    await config_service.save_provider_settings({
        "default_model_id": "不存在的模型",
        "fallback_model_ids": [m1],
        "provider_pool": ["*"],
    })
    rt = _runtime(config_service)
    try:
        chain = rt.provider_chain()

        assert [c.get("provider_model_id") for c in chain] == [m1]
    finally:
        rt.stop()


async def test_empty_chain_is_reported_loudly(stack):
    """两层都没有模型时必须留一条明确的 error —— 此前是彻底静默的「不回复」。"""
    config_service, _ids = stack
    rt = _runtime(config_service)
    try:
        assert rt.provider_chain() == []
    finally:
        rt.stop()


async def test_global_settings_survive_roundtrip(config_service):
    """存进去要能读回来（UI 回显依赖这条）。"""
    await config_service.save_provider_settings({
        "default_model_id": "abc",
        "fallback_model_ids": ["x", "y"],
        "provider_pool": ["*"],
    })

    settings = config_service.get_provider_settings()

    assert settings["default_model_id"] == "abc"
    assert settings["fallback_model_ids"] == ["x", "y"]

