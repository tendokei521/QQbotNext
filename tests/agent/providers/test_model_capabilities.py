"""从厂商 ``/models`` 返回推断模型能力（预勾选用的建议值）。

这里锁三条语义，因为它直接影响用户"要不要手动勾图像/工具调用"：

1. **能读到厂商元数据就以厂商为准**（OpenRouter 的 ``architecture`` + ``supported_parameters``、
   Gemini 的 ``supportedGenerationMethods``、Ollama ``/api/show`` 的 ``capabilities``）；
2. **读不到才按模型名补图像/语音**，且**绝不用名字猜工具调用**（猜错会让模型收到 tools 直接 400）；
3. **什么都读不到就用该能力类型的默认值**，并如实标 ``source="default"``——前端不会假装"已按厂商信息勾选"。
"""

from __future__ import annotations

from app.agent.providers.model_capabilities import (
    AUDIO,
    CANONICAL,
    IMAGE,
    TEXT,
    TOOL_USE,
    infer_modalities,
)


def test_openrouter_vision_and_tools():
    entry = {
        "id": "qwen/qwen2.5-vl-72b-instruct",
        "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
        "supported_parameters": ["temperature", "tools", "tool_choice"],
    }
    modalities, source = infer_modalities(entry)
    assert source == "metadata"
    assert modalities == [TEXT, IMAGE, TOOL_USE]


def test_openrouter_text_only_model_has_no_tool_use():
    """``supported_parameters`` 里没有 tools = 可信的"不支持工具调用"。"""
    entry = {
        "id": "deepseek/deepseek-chat",
        "architecture": {"input_modalities": ["text"]},
        "supported_parameters": ["temperature", "max_tokens"],
    }
    modalities, source = infer_modalities(entry)
    assert source == "metadata"
    assert modalities == [TEXT]


def test_gemini_supported_generation_methods():
    entry = {"id": "gemini-2.0-flash", "supportedGenerationMethods": ["generateContent", "countTokens"]}
    modalities, source = infer_modalities(entry)
    assert source == "metadata"
    assert modalities == [TEXT]


def test_ollama_capabilities_list():
    entry = {"id": "llava:13b", "capabilities": ["completion", "vision"]}
    modalities, source = infer_modalities(entry)
    assert source == "metadata"
    assert modalities == [TEXT, IMAGE]


def test_generic_boolean_flags():
    assert infer_modalities({"id": "m", "supports_tools": True, "supports_vision": False})[0] == [
        TEXT,
        TOOL_USE,
    ]
    assert infer_modalities({"id": "m", "supports_tools": True, "supports_vision": True})[0] == [
        TEXT,
        IMAGE,
        TOOL_USE,
    ]


def test_name_hint_only_fills_image_and_audio():
    """名字只能补图像/语音；工具调用不许按名字猜。"""
    modalities, source = infer_modalities({"id": "glm-4v-plus"})
    assert source == "name"
    assert modalities == [TEXT, IMAGE, TOOL_USE]  # tool_use 来自 chat 默认值，不是名字推断
    assert TOOL_USE in modalities

    plain, plain_source = infer_modalities({"id": "qwen2.5-72b-instruct"})
    assert plain_source == "default"
    assert plain == [TEXT, TOOL_USE]


def test_openai_compatible_without_metadata_uses_default():
    modalities, source = infer_modalities({"id": "gpt-3.5-turbo", "object": "model", "owned_by": "openai"})
    assert source == "default"
    assert modalities == [TEXT, TOOL_USE]


def test_defaults_follow_provider_type():
    assert infer_modalities({"id": "text-embedding-3-small"}, "embedding") == ([TEXT], "default")
    assert infer_modalities({"id": "bge-reranker"}, "rerank") == ([TEXT], "default")
    assert infer_modalities({"id": "some-tts"}, "tts")[0] == [AUDIO]
    assert infer_modalities({"id": "some-stt"}, "stt")[0] == [AUDIO]


def test_unknown_tokens_are_dropped():
    """不认识的能力名直接丢弃：宁可少勾（用户可改），不要勾出不存在的能力。"""
    modalities, source = infer_modalities({"id": "m", "capabilities": {"thinking": True, "tools": True}})
    assert source == "metadata"
    assert modalities == [TEXT, TOOL_USE]


def test_result_is_always_in_canonical_order():
    entry = {"id": "m", "capabilities": ["tools", "vision", "audio", "completion"]}
    modalities, _ = infer_modalities(entry)
    assert modalities == list(CANONICAL)


def test_empty_entry_is_safe():
    assert infer_modalities(None) == ([TEXT, TOOL_USE], "default")
    assert infer_modalities({}) == ([TEXT, TOOL_USE], "default")


# ==================== 目录接口（runtime_manager）====================


class _FakeConfigService:
    def get_provider_preset(self, preset_id):
        return {"id": preset_id, "name": "t", "provider": "openai", "config": {"api_base": "http://x/v1"}}


class _FakeProvider:
    """模拟 OpenRouter 风格的 ``/models`` 返回。"""

    def __init__(self, config):
        self.config = config

    async def get_model_catalog(self):
        return [
            {
                "id": "qwen/qwen2.5-vl-72b-instruct",
                "raw": {
                    "id": "qwen/qwen2.5-vl-72b-instruct",
                    "architecture": {"input_modalities": ["text", "image"]},
                    "supported_parameters": ["tools"],
                },
            },
            {"id": "glm-4v-plus", "raw": {"id": "glm-4v-plus"}},
            {"raw": {"id": "no-id-entry"}},
            "not-a-dict",
        ]

    async def get_models(self):
        raise AssertionError("有 get_model_catalog 时不该再单独调 get_models")


async def test_catalog_carries_inferred_modalities(monkeypatch):
    from app.agent.providers import runtime_manager as rm

    monkeypatch.setattr(rm, "get_provider_class", lambda provider: _FakeProvider)
    manager = rm.ProviderRuntimeManager(_FakeConfigService())

    catalog = await manager.fetch_model_catalog("p1")

    assert [item["id"] for item in catalog] == ["qwen/qwen2.5-vl-72b-instruct", "glm-4v-plus", "no-id-entry"]
    assert catalog[0]["modalities"] == [TEXT, IMAGE, TOOL_USE]
    assert catalog[0]["source"] == "metadata"
    assert catalog[1]["source"] == "name"
    assert catalog[2]["source"] == "default", "没有 id、也没有能力字段 → 默认值"


async def test_fetch_models_stays_a_plain_list(monkeypatch):
    """``fetch_models`` 必须保持旧契约（纯字符串数组），老调用方不受影响。"""
    from app.agent.providers import runtime_manager as rm

    monkeypatch.setattr(rm, "get_provider_class", lambda provider: _FakeProvider)
    manager = rm.ProviderRuntimeManager(_FakeConfigService())

    assert await manager.fetch_models("p1") == [
        "qwen/qwen2.5-vl-72b-instruct",
        "glm-4v-plus",
        "no-id-entry",
    ]


async def test_catalog_falls_back_to_get_models(monkeypatch):
    """没实现 ``get_model_catalog`` 的 Provider：退回 ``get_models``，能力走默认值。"""

    class _Legacy:
        def __init__(self, config):
            pass

        async def get_models(self):
            return ["gpt-3.5-turbo"]

    from app.agent.providers import runtime_manager as rm

    monkeypatch.setattr(rm, "get_provider_class", lambda provider: _Legacy)
    manager = rm.ProviderRuntimeManager(_FakeConfigService())

    catalog = await manager.fetch_model_catalog("p1")
    assert catalog == [{"id": "gpt-3.5-turbo", "modalities": [TEXT, TOOL_USE], "source": "default"}]
