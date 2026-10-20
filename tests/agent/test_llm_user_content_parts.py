"""``extra_user_content_parts``：user 侧额外内容块（对齐 AstrBot）。

AstrBot 把 ``<system_reminder>`` / ``<Quoted Message>`` / ``[Image Attachment: path …]``
这类内容作为 **user 消息的额外文本块**追加（``ProviderRequest.extra_user_content_parts``），
而不是新增一条 system 消息。QQbotNext 此前没有这个概念，1:1 复刻组装时无处安放。

本文件锁住：
1. 没有额外块时 content 仍是**纯字符串**（既有行为逐字不变）；
2. 有额外块时 content 变多块数组，顺序「正文 → 额外块」；
3. 与图片共存时顺序「正文 → 额外块 → 图片」（place_images 不再把额外块丢掉）；
4. 空白项丢弃；空正文只留额外块。
"""

from __future__ import annotations

import base64

from app.agent.assembly.assembler import PromptAssembler, PromptRequest

BASE_CONFIG = {"system_prompt": "你是助手", "outbound_directive_enable": False}

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32
PNG_B64 = base64.b64encode(PNG_BYTES).decode()
PNG_DATA_URL = f"data:image/png;base64,{PNG_B64}"
#: 图片条目现在是"本地字节"（base64），远端 URL 一律不进请求体
LOCAL_IMAGE = [{"kind": "base64", "value": PNG_B64, "mime": "image/png"}]


def _req(**kw) -> PromptRequest:
    base = dict(
        config=dict(BASE_CONFIG),
        session_id="group_1",
        user_text="你好",
        raw_user_text="你好",
        history=[],
        available_tools=set(),
    )
    base.update(kw)
    return PromptRequest(**base)


def _user_message(messages: list[dict]) -> dict:
    return [m for m in messages if m.get("role") == "user"][-1]


# ==================== 默认：仍是纯字符串 ====================


def test_no_parts_keeps_plain_string_content():
    messages = PromptAssembler().build(_req())
    assert _user_message(messages) == {"role": "user", "content": "你好"}


# ==================== 有额外块 ====================


def test_parts_make_content_blocks_after_text():
    req = _req(extra_user_content_parts=["<system_reminder>User ID: 1</system_reminder>"])
    content = _user_message(PromptAssembler().build(req))["content"]
    assert content == [
        {"type": "text", "text": "你好"},
        {"type": "text", "text": "<system_reminder>User ID: 1</system_reminder>"},
    ]


def test_multiple_parts_keep_their_order():
    req = _req(extra_user_content_parts=["<Quoted Message>\n引用内容\n</Quoted Message>", "[Image Attachment: path a.png]"])
    content = _user_message(PromptAssembler().build(req))["content"]
    assert [b["text"] for b in content] == [
        "你好",
        "<Quoted Message>\n引用内容\n</Quoted Message>",
        "[Image Attachment: path a.png]",
    ]


def test_blank_parts_are_dropped():
    req = _req(extra_user_content_parts=["", "   ", "\n", "有效块"])
    content = _user_message(PromptAssembler().build(req))["content"]
    assert content == [{"type": "text", "text": "你好"}, {"type": "text", "text": "有效块"}]


def test_empty_user_text_keeps_only_parts():
    """空正文 + 额外块：只留额外块，不产生空的 text 块。"""
    req = _req(user_text="", raw_user_text="", extra_user_content_parts=["<system_reminder>x</system_reminder>"])
    content = _user_message(PromptAssembler().build(req))["content"]
    assert content == [{"type": "text", "text": "<system_reminder>x</system_reminder>"}]


# ==================== 与图片共存 ====================


def _image_req(**kw) -> PromptRequest:
    kw.setdefault("modalities", ["text", "image"])
    kw.setdefault("user_images", LOCAL_IMAGE)
    return _req(**kw)


def test_place_images_keeps_parts_between_text_and_image():
    """图片归位不许把额外块丢掉，且顺序与 AstrBot assemble_context 一致。"""
    req = _image_req(extra_user_content_parts=["<system_reminder>id=1</system_reminder>"])
    content = _user_message(PromptAssembler().build(req))["content"]
    assert content == [
        {"type": "text", "text": "你好"},
        {"type": "text", "text": "<system_reminder>id=1</system_reminder>"},
        {"type": "image_url", "image_url": {"url": PNG_DATA_URL}},
    ]


def test_images_without_parts_keep_previous_order():
    """无额外块时图片顺序与既有行为一致（正文在前、图片在后）。"""
    content = _user_message(PromptAssembler().build(_image_req()))["content"]
    assert content[0] == {"type": "text", "text": "你好"}
    assert content[1]["type"] == "image_url"


def test_no_usable_image_keeps_text_and_parts():
    """图片拿不到（列表为空）时退回纯文本块数组，额外块仍在。"""
    req = _image_req(user_images=[], extra_user_content_parts=["块"])
    content = _user_message(PromptAssembler().build(req))["content"]
    assert content == [{"type": "text", "text": "你好"}, {"type": "text", "text": "块"}]


def test_model_without_image_modality_keeps_parts():
    """模型不支持图片时 place_images 不介入，额外块照常保留。"""
    req = _req(
        modalities=["text"],
        user_images=[{"kind": "url", "value": "https://a/1.png"}],
        extra_user_content_parts=["块"],
    )
    content = _user_message(PromptAssembler().build(req))["content"]
    assert content == [{"type": "text", "text": "你好"}, {"type": "text", "text": "块"}]
