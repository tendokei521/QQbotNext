"""内置预设新增的服务商，统计面板要显示成真实服务商，而不是适配器名 ``openai``。

``_api_label`` 按 ``api_base`` 主机名猜服务商；新加的一批预设（z.ai / token-plan /
kimi / minimax / 小米 / groq / cerebras / nvidia / together / huggingface / fireworks）
如果漏了映射，面板会把它们全记成 openai，作者自己也分不清钱花在哪。
"""

from __future__ import annotations

import pytest

from app.agent.usage.metered import _api_label


@pytest.mark.parametrize(
    ("api_base", "expected"),
    [
        ("https://api.z.ai/api/coding/paas/v4", "zhipu"),
        ("https://open.bigmodel.cn/api/coding/paas/v4", "zhipu"),
        ("https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1", "qwen"),
        ("https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1", "qwen"),
        ("https://api.kimi.com/coding/v1", "moonshot"),
        ("https://api.moonshot.cn/v1", "moonshot"),
        ("https://api.minimaxi.com/anthropic", "minimax"),
        ("https://api.minimax.io/anthropic", "minimax"),
        ("https://token-plan-cn.xiaomimimo.com/v1", "xiaomi"),
        ("https://api.groq.com/openai/v1", "groq"),
        ("https://api.cerebras.ai/v1", "cerebras"),
        ("https://integrate.api.nvidia.com/v1", "nvidia"),
        ("https://api.together.ai/v1", "together"),
        ("https://router.huggingface.co/v1", "huggingface"),
        ("https://api.fireworks.ai/inference/v1", "fireworks"),
        ("https://api.openai.com/v1", "openai"),
        ("https://api.deepseek.com", "deepseek"),
        ("https://opencode.ai/zen/go/v1", "opencode"),
        ("https://api.anthropic.com", "anthropic"),
        ("https://generativelanguage.googleapis.com/v1beta", "gemini"),
    ],
)
def test_api_label_maps_builtin_preset_hosts(api_base: str, expected: str):
    assert _api_label({"api_base": api_base, "provider": "openai"}) == expected


def test_api_label_falls_back_to_provider_name():
    """认不出主机时至少别显示错的服务商：退回适配器名。"""
    assert _api_label({"api_base": "https://my-gateway.internal/v1", "provider": "openai-responses"}) == "openai-responses"
