"""会话历史里的图片：URL 必须随会话走（视觉模型内联，文本模型留可展开占位）。

真实日志（private_1901691195 / 2026-10-18 22:49 → 23:04）：
用户先发了一张图，下一轮追问"所以你看得到这张图吗"——历史里那条只剩
``10-18 22:49 对方: (QQ: 1901691195)``，图片段（``base.segments`` 里明明存着 url）
被渲染层完全丢掉：模型既看不到图，也没有 ``[图片#id]`` 占位可去 ``expand_image``。

本文件钉住三件事：
1. 历史条目的图片段能被解析成图片块（纯本地，不发起 get_image）；
2. 正文为空（纯图片回合）时渲染成 ``[图片#<消息id>]``，不再退化成元信息；
3. 视觉模型：图片 url 内联进历史；文本模型：只有占位、没有图块。
"""

from __future__ import annotations

import types

import pytest

from app.domain.events import GroupMessageEvent, MessageSegment, UserInfo
from app.llm.image import history_image_parts, history_image_segments

URL = "https://multimedia.nt.qq.com.cn/download?fileid=abc&rkey=xyz"


def _entry(*, text: str = "", segments=None, message_id: str = "894061058") -> dict:
    return {
        "role": "user",
        "message_id": message_id,
        "base": {
            "time": 1792334953,
            "sender_id": "1901691195",
            "sender_name": "桉",
            "is_private": True,
            "text": text,
            "segments": segments if segments is not None else [
                {"type": "image", "data": {"url": URL, "file": "x.jpg"}},
            ],
        },
        "content": text,
    }


# ---------- 段 → 图片块（纯本地） ----------


def test_history_image_segments_reads_base_segments():
    segs = history_image_segments(_entry())
    assert [s["type"] for s in segs] == ["image"]


def test_history_image_parts_inlines_stored_url():
    assert history_image_parts(_entry()) == [
        {"type": "image_url", "image_url": {"url": URL}},
    ]


def test_history_image_parts_accepts_data_url_and_base64():
    data_url = "data:image/png;base64,AAAA"
    from_data = _entry(segments=[{"type": "image", "data": {"url": data_url}}])
    assert history_image_parts(from_data)[0]["image_url"]["url"] == data_url

    import base64

    raw = base64.b64encode(b"\x89PNG\r\n\x1a\n1234").decode("ascii")
    from_b64 = _entry(segments=[{"type": "image", "data": {"file": "x.image", "base64": f"base64://{raw}"}}])
    part = history_image_parts(from_b64)[0]["image_url"]["url"]
    assert part.startswith("data:image/png;base64,")


def test_history_image_parts_skips_file_only_segment():
    """只有 QQ 内部文件名（需要 get_image 换直链）→ 不内联，留 [图片#id] 占位。"""
    entry = _entry(segments=[{"type": "image", "data": {"file": "7946D3B0.png"}}])
    assert history_image_parts(entry) == []


def test_history_image_parts_respects_max_images():
    entry = _entry(segments=[
        {"type": "image", "data": {"url": f"https://cdn.example/{i}.png"}} for i in range(5)
    ])
    assert len(history_image_parts(entry, max_images=2)) == 2


# ---------- 渲染：正文为空时给占位，视觉模型再内联 ----------


def _hook(*, include_images: bool, entries: list, max_images: int = 4):
    from app.llm.chat import _history_media_hook

    return _history_media_hook(entries, include_images=include_images, max_images=max_images)


def test_image_only_entry_renders_placeholder_with_sender_prefix():
    from app.llm.group_context import format_history_for_llm

    entries = [_entry(text="")]
    rendered = format_history_for_llm(entries, is_private=True, entry_media=_hook(include_images=False, entries=entries))

    assert rendered[0]["content"] == "10-18 22:49 对方: [图片#894061058]"


def test_vision_hook_inlines_image_into_history_message():
    from app.llm.group_context import format_history_for_llm

    entries = [_entry(text="")]
    rendered = format_history_for_llm(entries, is_private=True, entry_media=_hook(include_images=True, entries=entries))

    content = rendered[0]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text" and "[图片#894061058]" in content[0]["text"]
    assert content[1] == {"type": "image_url", "image_url": {"url": URL}}


def test_text_hook_keeps_history_text_only():
    from app.llm.group_context import format_history_for_llm

    entries = [_entry(text="你看看这个")]
    rendered = format_history_for_llm(entries, is_private=True, entry_media=_hook(include_images=False, entries=entries))

    assert isinstance(rendered[0]["content"], str)
    assert "image_url" not in str(rendered[0]["content"])


# ---------- 端到端：第二轮请求里能带回上一轮的图片 ----------


def _img_event(t: int, mid: int) -> GroupMessageEvent:
    return GroupMessageEvent(
        event_type="message_group",
        message_type="group",
        time=t,
        message_id=mid,
        user_id=20002,
        self_id=778,
        message=[MessageSegment("image", {"url": URL, "file": "x.jpg"})],
        user=UserInfo(user_id=20002, nickname="小明"),
        group=types.SimpleNamespace(group_id=466052056, group_name="测试群"),
    )


def _text_event(t: int, mid: int, text: str) -> GroupMessageEvent:
    return GroupMessageEvent(
        event_type="message_group",
        message_type="group",
        time=t,
        message_id=mid,
        user_id=20002,
        self_id=778,
        message=[MessageSegment("text", {"text": text})],
        user=UserInfo(user_id=20002, nickname="小明"),
        group=types.SimpleNamespace(group_id=466052056, group_name="测试群"),
    )


async def _two_turns(monkeypatch, tmp_path, *, modalities, session_id: str):
    from app.llm import chat
    from app.llm.providers import PROVIDERS
    from tests.test_llm_assembly_paths import (
        CHAT_MESSAGES,
        _config_dict,
        _ctx,
        _ProbeProvider,
        _runtime,
    )

    monkeypatch.setitem(PROVIDERS, "probe_model", _ProbeProvider)
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    CHAT_MESSAGES.clear()
    rt = _runtime(_config_dict())
    if modalities is not None:
        rt.provider_chain = lambda: [
            {"provider": "probe_model", "model": "probe-model", "modalities": list(modalities)}
        ]

    # 第一轮：只发图（user_text 是增强块形态，正文为空）
    event1 = _img_event(1788342260, 889196000)
    ctx1 = _ctx(event1, session_id=session_id)
    ctx1.user_text = "\n".join(["(当前群号: 466052056)", "发送者：小明(20002)", "发送了："])
    await chat.generate_response(rt, event1, ctx1)

    # 第二轮：追问
    event2 = _text_event(1788343000, 889196001, "这张图里是什么")
    ctx2 = _ctx(event2, session_id=session_id)
    ctx2.user_text = "\n".join(["(当前群号: 466052056)", "发送者：小明(20002)", "发送了：这张图里是什么"])
    await chat.generate_response(rt, event2, ctx2)
    return CHAT_MESSAGES[-1]


@pytest.mark.asyncio
async def test_vision_model_gets_previous_turn_image_in_history(monkeypatch, tmp_path):
    sent = await _two_turns(
        monkeypatch, tmp_path, modalities=["text", "image"],
        session_id="group_466052056_hist_img_vision",
    )

    inlined = [
        m for m in sent
        if m["role"] == "user" and isinstance(m.get("content"), list)
        and any(p.get("type") == "image_url" and p["image_url"]["url"] == URL for p in m["content"])
    ]
    assert inlined, sent
    text_part = inlined[0]["content"][0]["text"]
    assert "[图片#889196000]" in text_part


@pytest.mark.asyncio
async def test_text_model_history_keeps_placeholder_only(monkeypatch, tmp_path):
    sent = await _two_turns(
        monkeypatch, tmp_path, modalities=["text", "tool_use"],
        session_id="group_466052056_hist_img_text",
    )

    assert not any(
        isinstance(m.get("content"), list)
        and any(p.get("type") == "image_url" for p in m["content"])
        for m in sent
    )
    assert any(
        isinstance(m.get("content"), str) and "[图片#889196000]" in m["content"]
        for m in sent if m["role"] == "user"
    ), sent


@pytest.mark.asyncio
async def test_current_turn_is_not_duplicated_in_history(monkeypatch, tmp_path):
    """当前这条不能既在历史里、又当本轮消息发一遍（图片会因此传两遍）。"""
    sent = await _two_turns(
        monkeypatch, tmp_path, modalities=["text", "image"],
        session_id="group_466052056_hist_img_dedup",
    )

    occurrences = [
        m for m in sent
        if m["role"] == "user" and "这张图里是什么" in str(m.get("content"))
    ]
    assert len(occurrences) == 1, sent
