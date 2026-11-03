"""Anthropic/Gemini 的 message.name 兼容（A 方案 fallback）。

OpenAI 兼容链路可以带 ``name`` 字段；Anthropic/Gemini 没有参与者名字段，
适配层必须按**旧实现**把昵称内联回正文：
``[time=..][sender:id=..,name=".."]正文``（JSON 转义，避免昵称破坏标记）。
"""

from __future__ import annotations

from app.agent.providers.anthropic import _normalize_messages as anthropic_messages
from app.agent.providers.base import inline_message_name
from app.agent.providers.gemini import _normalize_messages as gemini_messages

NAME = '老师"今年的"学费\n交吗'
CONTENT = '[time=2026-11-03 19:06,message_id=1][sender:id=1901691195]小绿在吗'


def test_inline_name_replaces_sender_id_marker():
    out = inline_message_name(CONTENT, NAME)
    assert out.startswith('[time=2026-11-03 19:06,message_id=1][sender:id=1901691195,name=')
    assert out.endswith("]小绿在吗")
    assert '\\"今年的\\"' in out and "\\n" in out, "昵称必须 JSON 转义"


def test_inline_name_without_sender_marker_prefixes_old_style():
    out = inline_message_name("正文", "小明")
    assert out == '[sender:name="小明"]正文'


def test_inline_name_handles_content_blocks():
    blocks = [{"type": "text", "text": CONTENT}, {"type": "image_url", "image_url": {"url": "x"}}]
    out = inline_message_name(blocks, "小明")
    assert 'name="小明"' in out[0]["text"]
    assert out[1] == blocks[1]


def test_anthropic_normalizer_inlines_name_and_drops_field():
    system, chat = anthropic_messages([
        {"role": "user", "name": NAME, "content": CONTENT},
    ])
    assert system is None
    text = chat[0]["content"][0]["text"]
    assert '[sender:id=1901691195,name=' in text
    assert "小绿在吗" in text
    assert "name" not in chat[0]


def test_gemini_normalizer_inlines_name_and_drops_field():
    contents, system = gemini_messages([
        {"role": "user", "name": NAME, "content": CONTENT},
    ])
    assert system is None
    parts = contents[0]["parts"]
    assert '[sender:id=1901691195,name=' in parts[0]["text"]
    assert "小绿在吗" in parts[0]["text"]
