"""模态能力工具测试。"""

from app.llm.providers.modalities import (
    default_modalities_for,
    normalize_modalities,
    sanitize_contexts_by_modalities,
    supports_audio,
    supports_image,
    supports_tool_use,
)


def test_normalize_modalities():
    assert normalize_modalities(None) is None
    assert normalize_modalities(["text", "image", "bad", "image", "tool_use"]) == [
        "text",
        "image",
        "tool_use",
    ]
    assert normalize_modalities("text") is None


def test_default_modalities_for():
    assert default_modalities_for("chat") == ["text", "tool_use"]
    assert default_modalities_for("tts") == ["audio"]
    assert default_modalities_for("embedding") == ["text"]
    assert default_modalities_for("unknown") == ["text"]


def test_supports_legacy_unset_is_permissive():
    assert supports_image(None) is True
    assert supports_audio(None) is True
    assert supports_tool_use(None) is True


def test_sanitize_no_config_keeps_messages():
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    result = sanitize_contexts_by_modalities(messages, None)
    assert result == messages


def test_sanitize_tool_use_disabled():
    messages = [
        {"role": "tool", "tool_call_id": "x", "content": "result"},
        {"role": "assistant", "tool_calls": [{"id": "x"}], "content": None},
    ]
    result = sanitize_contexts_by_modalities(messages, ["text"])
    assert result[0]["role"] == "user"
    assert "[Tool result]" in result[0]["content"]
    assert "tool_calls" not in result[1]


def test_sanitize_multimodal_blocks():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "看这个"},
                {"type": "image_url", "image_url": {"url": "x"}},
                {"type": "audio_url", "audio_url": {"url": "y"}},
            ],
        }
    ]
    # 不支持 image/audio
    result = sanitize_contexts_by_modalities(messages, ["text"])
    assert result[0]["content"] == [
        {"type": "text", "text": "看这个"},
        {"type": "text", "text": "[Image]"},
        {"type": "text", "text": "[Audio]"},
    ]
    # 支持 image、不支持 audio
    result = sanitize_contexts_by_modalities(messages, ["text", "image"])
    assert result[0]["content"] == [
        {"type": "text", "text": "看这个"},
        {"type": "image_url", "image_url": {"url": "x"}},
        {"type": "text", "text": "[Audio]"},
    ]
    # 全部支持时原样保留
    result = sanitize_contexts_by_modalities(messages, ["text", "image", "audio", "tool_use"])
    assert result == messages
