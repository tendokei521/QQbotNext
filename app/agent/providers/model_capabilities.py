"""从厂商 ``/models`` 返回里推断「这个模型支持哪些能力」。

## 为什么需要

模型的「支持能力」（文本 / 图像 / 语音 / 工具调用）现在是手勾的，而用户往往不知道
哪个模型能看图、哪个能调工具。厂商的模型列表**有时**带能力信息：

| 厂商 | 字段 | 能读到什么 |
|---|---|---|
| OpenRouter | ``architecture.input_modalities``、``supported_parameters`` | 输入模态（text/image）、是否支持 tools |
| Google Gemini | ``supportedGenerationMethods`` | 是否 generateContent / embedContent |
| Ollama ``/api/show`` | ``capabilities`` | completion / tools / vision / thinking |
| 部分中转 | ``capabilities`` / ``features`` / ``modalities`` | 各家自定义的布尔或字符串数组 |
| 标准 OpenAI | 无 | 只有 id / owned_by，**什么能力信息都没有** |

所以这里做三档推断，并如实告诉调用方结论是哪一档来的：

1. ``metadata``：厂商返回了能读懂的能力字段（可信，直接预勾选）。对话模型额外
   **默认补上工具调用**——厂商的元数据几乎不声明它，不补就会把"能调工具的模型"
   预勾成纯文本/图片，框架随后按"不支持工具调用"把 tools 全部剔除；
   只有厂商**明确否定**（OpenRouter ``supported_parameters`` 不含 tools、
   或 ``supports_tools: false``）才不补；
2. ``name``：没有元数据，但模型名带强线索（``-vl`` / ``-vision`` / ``gpt-4o`` …），
   只用来**补**图像/语音，不动工具调用；
3. ``default``：什么都没有，用该能力类型的默认值（对话模型 = 文本 + 工具调用）。

推断只在「添加模型」时用于预勾选，用户随时可以改；不做任何强制。
"""

from __future__ import annotations

import re
from typing import Any

TEXT = "text"
IMAGE = "image"
AUDIO = "audio"
TOOL_USE = "tool_use"

#: 规范顺序（预勾选按这个顺序展示，避免不同厂商字段顺序导致界面抖动）
CANONICAL: tuple[str, ...] = (TEXT, IMAGE, AUDIO, TOOL_USE)

#: 各能力类型的默认值（"厂商没说，那就按这类模型的常识来"）
DEFAULTS: dict[str, tuple[str, ...]] = {
    "chat": (TEXT, TOOL_USE),
    "embedding": (TEXT,),
    "rerank": (TEXT,),
    "tts": (AUDIO,),
    "stt": (AUDIO,),
}

_SOURCE_METADATA = "metadata"
_SOURCE_NAME = "name"
_SOURCE_DEFAULT = "default"

#: 各家写法 → 我们的能力名
_ALIASES: dict[str, str] = {
    # 文本
    "text": TEXT,
    "completion": TEXT,
    "chat": TEXT,
    "generatecontent": TEXT,
    "generate_content": TEXT,
    "input_text": TEXT,
    "output_text": TEXT,
    # 图像
    "image": IMAGE,
    "images": IMAGE,
    "vision": IMAGE,
    "image_input": IMAGE,
    "input_image": IMAGE,
    "multimodal": IMAGE,
    "input_images": IMAGE,
    # 语音
    "audio": AUDIO,
    "speech": AUDIO,
    "voice": AUDIO,
    "audio_input": AUDIO,
    "input_audio": AUDIO,
    "tts": AUDIO,
    "stt": AUDIO,
    # 工具调用
    "tools": TOOL_USE,
    "tool": TOOL_USE,
    "tool_use": TOOL_USE,
    "tool_calling": TOOL_USE,
    "tool_calls": TOOL_USE,
    "function_calling": TOOL_USE,
    "function_calls": TOOL_USE,
}

#: 模型名里的强线索（只补图像/语音；工具调用不做名字推断——猜错会让模型收到 tools 直接 400）
_IMAGE_NAME_RE = re.compile(
    r"(?:^|[^a-z])(?:vl|vision|llava|pixtral|internvl|minicpm-v|omni"
    r"|gpt-4o|gpt-4\.1|gpt-5|gemini-[12]|claude-(?:3|4)|glm-4v|doubao-vision|step-1v)(?:[^a-z]|$)",
    re.IGNORECASE,
)
_AUDIO_NAME_RE = re.compile(
    r"(?:^|[^a-z])(?:whisper|tts|asr|audio|realtime|voice|speech)(?:[^a-z]|$)",
    re.IGNORECASE,
)


def _ordered(modalities: set[str]) -> list[str]:
    """按 ``CANONICAL`` 排序，未知值丢弃（宁可少勾，不要勾出根本不存在的能力）。"""
    return [item for item in CANONICAL if item in modalities]


def _add_tokens(found: set[str], value: Any) -> None:
    """把 ``["tools", "vision"]`` / ``{"vision": true, "tools": false}`` 收进 found。"""
    if isinstance(value, dict):
        for key, flag in value.items():
            if flag is True or (isinstance(flag, str) and flag.lower() in ("true", "1", "yes")):
                _add_tokens(found, key)
        return
    if isinstance(value, str):
        values: list[Any] = [value]
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        return
    for item in values:
        if not isinstance(item, str):
            continue
        mapped = _ALIASES.get(item.strip().lower())
        if mapped:
            found.add(mapped)


def _metadata_modalities(entry: dict) -> set[str]:
    """读厂商返回里**任何**能识别的能力字段（多来源取并集）。"""
    found: set[str] = set()

    for key in ("capabilities", "features", "modalities", "input_modalities", "output_modalities"):
        _add_tokens(found, entry.get(key))

    architecture = entry.get("architecture")
    if isinstance(architecture, dict):
        _add_tokens(found, architecture.get("input_modalities"))
        _add_tokens(found, architecture.get("output_modalities"))
        _add_tokens(found, architecture.get("modality"))

    # Gemini：supportedGenerationMethods / supported_generation_methods
    methods = entry.get("supportedGenerationMethods") or entry.get("supported_generation_methods")
    _add_tokens(found, methods)

    # OpenRouter：supported_parameters 里出现 "tools" = 支持工具调用。
    # 这个字段几乎总会返回，所以它同时也是"不支持工具"的**可信否定**。
    params = entry.get("supported_parameters") or entry.get("supportedParameters")
    supports_tools: bool | None = None
    if isinstance(params, (list, tuple, set)):
        names = {str(item).strip().lower() for item in params if isinstance(item, str)}
        if "tools" in names or "tool_choice" in names:
            found.add(TOOL_USE)
            supports_tools = True
        elif names:
            supports_tools = False
    elif isinstance(entry.get("tool_use"), bool):
        supports_tools = bool(entry["tool_use"])

    if supports_tools is False:
        found.discard(TOOL_USE)

    # 显式布尔：supports_tools / supports_vision …
    for key, modality in (("supports_tools", TOOL_USE), ("supports_vision", IMAGE), ("supports_audio", AUDIO)):
        flag = entry.get(key)
        if isinstance(flag, bool):
            found.add(modality) if flag else found.discard(modality)

    return found


def _metadata_denies_tools(entry: dict) -> bool:
    """厂商**明确否定**工具调用时返回 True。

    只认可信的否定来源：OpenRouter ``supported_parameters`` 里有参数列表却不含
    tools/tool_choice，或显式 ``supports_tools: false`` / ``tool_use: false``。
    「字段里没提工具调用」**不算**否定——绝大多数厂商的能力元数据根本不声明它。
    """
    params = entry.get("supported_parameters") or entry.get("supportedParameters")
    if isinstance(params, (list, tuple, set)):
        names = {str(item).strip().lower() for item in params if isinstance(item, str)}
        if names and "tools" not in names and "tool_choice" not in names:
            return True
    for key in ("supports_tools", "tool_use"):
        if entry.get(key) is False:
            return True
    return False


def infer_modalities(entry: dict | None, provider_type: str = "chat") -> tuple[list[str], str]:
    """推断一个模型的能力，返回 ``(modalities, source)``。

    - ``source == "metadata"``：来自厂商返回的能力字段；
    - ``source == "name"``：厂商没说，按模型名强线索补了图像/语音；
    - ``source == "default"``：什么都没读到，用该能力类型的默认值。
    """
    data = entry if isinstance(entry, dict) else {}
    kind = str(provider_type or "chat").strip().lower()
    defaults = set(DEFAULTS.get(kind, DEFAULTS["chat"]))

    from_metadata = _metadata_modalities(data)
    if from_metadata:
        # 能读懂的模型至少都处理文本输入（embedding/rerank 也一样）；
        # 语音类（tts/stt）不因为"有元数据"就凭空多出 text。
        if kind in ("chat", "embedding", "rerank", ""):
            from_metadata.add(TEXT)
        # 工具调用是对话模型的通用能力，而厂商元数据通常不声明它（OpenAI 兼容
        # 接口尤其如此）。不补的话，这里预勾成「文本(+图片)」，框架就认为模型
        # 不支持工具调用 → 请求里 tools=0 → 参与度模式下模型只能输出文本（思考），
        # 表现为「机器人永远不回复」。
        if kind in ("chat", "agent", "") and not _metadata_denies_tools(data):
            from_metadata.add(TOOL_USE)
        return _ordered(from_metadata), _SOURCE_METADATA

    name = str(data.get("id") or data.get("name") or data.get("model") or "")
    guessed: set[str] = set()
    if name:
        if _IMAGE_NAME_RE.search(name):
            guessed.add(IMAGE)
        if _AUDIO_NAME_RE.search(name):
            guessed.add(AUDIO)
    if guessed:
        return _ordered(defaults | guessed), _SOURCE_NAME

    return _ordered(defaults), _SOURCE_DEFAULT


SOURCE_LABELS: dict[str, str] = {
    _SOURCE_METADATA: "服务端返回了能力信息",
    _SOURCE_NAME: "按模型名推断（服务端未返回能力信息）",
    _SOURCE_DEFAULT: "服务端未返回能力信息，用默认值",
}
