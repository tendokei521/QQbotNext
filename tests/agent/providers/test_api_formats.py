"""四种「API 格式」必须真的落到各自的适配器上。

预设页的「新建自定义预设」只给四个选项（Anthropic Messages / OpenAI Chat
Completions / OpenAI Responses / Gemini Native）；这里把它们与 ``PROVIDERS``
钉在一起，避免以后改名时前端还能选、后端却悄悄回退到 OpenAI 兼容。
"""

from __future__ import annotations

import pytest

from app.agent.providers import get_provider
from app.agent.providers.anthropic import AnthropicProvider
from app.agent.providers.gemini import GeminiProvider
from app.agent.providers.openai_compat import OpenAICompatProvider
from app.agent.providers.openai_responses import OpenAIResponsesProvider


@pytest.mark.parametrize(
    ("api_format", "expected"),
    [
        ("anthropic", AnthropicProvider),
        ("openai", OpenAICompatProvider),
        ("openai-responses", OpenAIResponsesProvider),
        ("gemini", GeminiProvider),
    ],
)
def test_api_format_resolves_to_its_adapter(api_format, expected):
    provider = get_provider({"provider": api_format, "api_key": "sk-test", "api_base": "https://example.com"})
    assert isinstance(provider, expected)
