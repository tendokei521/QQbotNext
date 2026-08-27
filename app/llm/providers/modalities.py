"""模型模态/能力工具。

对齐 AstrBot 的 ``modalities`` 设计：模型实例除了“能力类型”
（chat/embedding/rerank/tts/stt）外，还可以声明自己支持哪些输入模态，
例如图像、音频、工具调用。

- 未配置（``None``）时视为兼容旧行为：默认所有能力都放行。
- 显式配置为空列表时视为“仅文本”（图片/音频/工具调用均被清洗/禁用）。
"""

from __future__ import annotations

import copy
from typing import Any

SUPPORTED_MODALITIES = ("text", "image", "audio", "tool_use")

# 每个能力类型的合理默认值（仅用于新建模型时预填；旧模型保持 None=兼容旧行为）
_DEFAULT_MODALITIES: dict[str, list[str]] = {
    "chat": ["text", "tool_use"],
    "agent": ["text", "tool_use"],
    "embedding": ["text"],
    "rerank": ["text"],
    "tts": ["audio"],
    "stt": ["audio"],
}


def normalize_modalities(value: Any) -> list[str] | None:
    """把任意输入归一化为合法模态列表。

    - ``None`` -> ``None``（代表未配置/兼容旧行为）
    - 非列表 -> ``None``
    - 自动去重并过滤非法值，保留声明顺序
    """
    if value is None:
        return None
    if not isinstance(value, (list, tuple, set)):
        return None
    seen: list[str] = []
    for item in value:
        text = str(item).strip().lower()
        if text in SUPPORTED_MODALITIES and text not in seen:
            seen.append(text)
    return seen


def default_modalities_for(provider_type: str) -> list[str]:
    """返回指定能力类型的默认模态（新建模型时使用）。"""
    key = str(provider_type or "chat").strip().lower()
    return list(_DEFAULT_MODALITIES.get(key, ["text"]))


def supports(modalities: list[str] | None, key: str) -> bool:
    """判断是否支持某个模态；未配置时兼容旧行为返回 True。"""
    return modalities is None or key in modalities


def supports_image(modalities: list[str] | None) -> bool:
    return supports(modalities, "image")


def supports_audio(modalities: list[str] | None) -> bool:
    return supports(modalities, "audio")


def supports_tool_use(modalities: list[str] | None) -> bool:
    return supports(modalities, "tool_use")


def _tool_result_placeholder(content: Any) -> str:
    """把工具结果降级为纯文本占位。"""
    if isinstance(content, str):
        content_text = content.strip()
    elif isinstance(content, list):
        text_parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                part_type = str(part.get("type", "")).lower()
                if part_type == "text":
                    text_parts.append(str(part.get("text", "")))
                elif part_type in {"image_url", "image"}:
                    text_parts.append("[Image]")
                elif part_type in {"audio_url", "input_audio"}:
                    text_parts.append("[Audio]")
        content_text = "\n".join(text_parts).strip()
    else:
        content_text = ""
    if not content_text:
        return "[Tool result]"
    return f"[Tool result]\n{content_text}"


def sanitize_contexts_by_modalities(
    messages: list[dict[str, Any]],
    modalities: list[str] | None,
) -> list[dict[str, Any]]:
    """按模型能力清洗上下文。

    - 不支持工具调用：``role=tool`` 降级为 user 占位；``assistant.tool_calls`` 移除
    - 不支持图像：多模态消息块 ``image_url/image`` 替换为 ``[Image]``
    - 不支持音频：多模态消息块 ``audio_url/input_audio`` 替换为 ``[Audio]``

    未配置（``modalities is None``）时原样返回，保持旧行为。
    """
    if not messages:
        return []
    if modalities is None:
        return list(messages)

    can_image = supports_image(modalities)
    can_audio = supports_audio(modalities)
    can_tool_use = supports_tool_use(modalities)

    if can_image and can_audio and can_tool_use:
        return list(messages)

    sanitized: list[dict[str, Any]] = []
    for raw_msg in messages:
        if not isinstance(raw_msg, dict):
            sanitized.append(raw_msg)
            continue
        msg = copy.deepcopy(raw_msg)
        role = msg.get("role")

        if not can_tool_use:
            if role == "tool":
                msg = {
                    "role": "user",
                    "content": _tool_result_placeholder(msg.get("content")),
                }
            elif role == "assistant":
                if "tool_calls" in msg:
                    msg.pop("tool_calls", None)
                if "tool_call_id" in msg:
                    msg.pop("tool_call_id", None)

        if not can_image or not can_audio:
            content = msg.get("content")
            if isinstance(content, list):
                filtered_parts: list[Any] = []
                for part in content:
                    if isinstance(part, dict):
                        part_type = str(part.get("type", "")).lower()
                        if not can_image and part_type in {"image_url", "image"}:
                            filtered_parts.append({"type": "text", "text": "[Image]"})
                            continue
                        if not can_audio and part_type in {
                            "audio_url",
                            "input_audio",
                        }:
                            filtered_parts.append({"type": "text", "text": "[Audio]"})
                            continue
                    filtered_parts.append(part)
                msg["content"] = filtered_parts

        sanitized.append(msg)

    return sanitized
