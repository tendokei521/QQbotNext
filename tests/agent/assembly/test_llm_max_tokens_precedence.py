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

from app.agent.loop import chat
from app.agent.providers.runtime_manager import ProviderRuntimeManager
from app.agent.runtime import _origin_of
from app.application.provider_model_service import ProviderModelService

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


