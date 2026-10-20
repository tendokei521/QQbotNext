"""会话历史里的图片：**本地指向**随会话走（视觉模型内联 base64，文本模型留可展开占位）。

真实日志（private_1901691195 / 2026-10-18 22:49 → 23:04）：
用户先发了一张图，下一轮追问"所以你看得到这张图吗"——历史里那条只剩
``10-18 22:49 对方: (QQ: 1901691195)``，图片段（``base.segments``）被渲染层完全丢掉：
模型既看不到图，也没有 ``[图片#id]`` 占位可去 ``expand_image``。

随后的线上事故：历史里存的是 QQ 图片**直链**（带 rkey，几小时过期），第二轮把直链
发给网关 → 网关拉不到 → **整条请求 400**，而且每轮都失败一次。所以现在的口径是
"收到即落盘，历史只留本地指向"，本文件同时钉住这两件事：

1. 历史条目的图片段能被解析成图片块；
2. 正文为空（纯图片回合）时渲染成 ``[图片#<消息id>]``，不再退化成元信息；
3. 视觉模型：**本地字节内联成 base64**；文本模型：只有占位、没有图块；
4. **远端直链永远不进请求体**（只有指向、没有本地文件时退化成占位）。
"""

from __future__ import annotations

import base64
import types

import pytest

from app.domain.events import GroupMessageEvent, MessageSegment, UserInfo
from app.agent.media import image as image_mod
from app.agent.media import store as image_media
from app.agent.media.image import history_image_parts, history_image_segments

URL = "https://multimedia.nt.qq.com.cn/download?fileid=abc&rkey=xyz"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32
PNG_B64 = base64.b64encode(PNG_BYTES).decode()
BOT_ID = 778


@pytest.fixture
def media_ref():
    """落一张真实图片到本地仓，返回它的指向。"""
    ref = image_media.save_bytes(BOT_ID, PNG_BYTES)
    assert ref
    return ref


@pytest.fixture
def offline_download(monkeypatch):
    """把"下载远程图片"换成返回固定 PNG 字节（测试全程不联网）。"""

    async def _download(url, *, max_bytes, timeout=0):
        return PNG_BYTES, image_media.sniff_mime(PNG_BYTES)

    monkeypatch.setattr(image_mod, "_download_bytes", _download)
    return _download


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


def _entry_with_media(ref: dict, *, text: str = "", message_id: str = "894061058") -> dict:
    return _entry(
        text=text,
        message_id=message_id,
        segments=[{"type": "image", "data": {"media": dict(ref)}}],
    )


# ---------- 段 → 图片块（只认本地指向） ----------


def test_history_image_segments_reads_base_segments():
    segs = history_image_segments(_entry())
    assert [s["type"] for s in segs] == ["image"]


def test_history_image_parts_inlines_local_bytes_as_base64(media_ref):
    parts = history_image_parts(_entry_with_media(media_ref), bot_id=BOT_ID)

    assert len(parts) == 1
    url = parts[0]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert url.split(",", 1)[1] == PNG_B64


def test_history_image_parts_never_inlines_remote_url():
    """**核心回归**：历史里只有远端直链 → 不内联（那正是整轮 400 的源头）。"""
    assert history_image_parts(_entry(), bot_id=BOT_ID) == []


def test_history_image_parts_skips_missing_file(media_ref):
    """指向还在但文件被清掉了 → 占位，不报错。"""
    path = image_media.path_of(BOT_ID, media_ref["sha"])
    assert path
    import os

    os.remove(path)

    assert history_image_parts(_entry_with_media(media_ref), bot_id=BOT_ID) == []


def test_history_image_parts_skips_file_only_segment():
    """只有 QQ 内部文件名（需要 get_image 换直链）→ 不内联，留 [图片#id] 占位。"""
    entry = _entry(segments=[{"type": "image", "data": {"file": "7946D3B0.png"}}])
    assert history_image_parts(entry, bot_id=BOT_ID) == []


def test_history_image_parts_respects_max_images():
    refs = [image_media.save_bytes(BOT_ID, PNG_BYTES + bytes([i])) for i in range(5)]
    entry = _entry(segments=[{"type": "image", "data": {"media": dict(ref)}} for ref in refs])

    assert len(history_image_parts(entry, bot_id=BOT_ID, max_images=2)) == 2


# ---------- 渲染：正文为空时给占位，视觉模型再内联 ----------


def _hook(*, include_images: bool, entries: list, max_images: int = 4):
    from app.agent.loop.chat import _history_media_hook

    return _history_media_hook(
        entries, include_images=include_images, bot_id=BOT_ID, max_images=max_images
    )


def test_image_only_entry_renders_placeholder_with_sender_prefix():
    from app.agent.context.group import format_history_for_llm

    entries = [_entry(text="")]
    rendered = format_history_for_llm(entries, is_private=True, entry_media=_hook(include_images=False, entries=entries))

    assert rendered[0]["content"] == "10-18 22:49 对方: [图片#894061058]"


def test_vision_hook_inlines_local_image_into_history_message(media_ref):
    from app.agent.context.group import format_history_for_llm

    entries = [_entry_with_media(media_ref, text="")]
    rendered = format_history_for_llm(entries, is_private=True, entry_media=_hook(include_images=True, entries=entries))

    content = rendered[0]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text" and "[图片#894061058]" in content[0]["text"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_text_hook_keeps_history_text_only(media_ref):
    from app.agent.context.group import format_history_for_llm

    entries = [_entry_with_media(media_ref, text="你看看这个")]
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
    from app.agent.loop import chat
    from app.agent.providers import PROVIDERS
    from tests.test_llm_assembly_paths import (
        CHAT_MESSAGES,
        _config_dict,
        _ctx,
        _ProbeProvider,
        _runtime,
    )

    async def _download(url, *, max_bytes, timeout=0):
        return PNG_BYTES, image_media.sniff_mime(PNG_BYTES)

    monkeypatch.setattr(image_mod, "_download_bytes", _download)
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
        and any(
            p.get("type") == "image_url"
            and str(p["image_url"]["url"]).startswith("data:image/png;base64,")
            for p in m["content"]
        )
    ]
    assert inlined, sent
    text_part = inlined[0]["content"][0]["text"]
    assert "[图片#889196000]" in text_part


@pytest.mark.asyncio
async def test_request_never_carries_an_expiring_remote_image_url(monkeypatch, tmp_path):
    """把整条请求体翻一遍：图片只能是 data: 本地字节，不能出现 QQ 直链。"""
    sent = await _two_turns(
        monkeypatch, tmp_path, modalities=["text", "image"],
        session_id="group_466052056_hist_img_no_url",
    )

    for message in sent:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if part.get("type") != "image_url":
                continue
            assert str(part["image_url"]["url"]).startswith("data:image/"), part


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


@pytest.mark.asyncio
async def test_history_backfill_turns_remote_url_into_local_ref(monkeypatch):
    """改造前的老条目只有直链：回填后要变成本地指向，且直链被抹掉。"""
    from app.agent.media.image import backfill_history_media

    async def _download(url, *, max_bytes, timeout=0):
        return PNG_BYTES, image_media.sniff_mime(PNG_BYTES)

    monkeypatch.setattr(image_mod, "_download_bytes", _download)
    # 老形状：段里只有 url（新条目不会这样存）
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(__import__("tempfile").mkdtemp()))
    entry = _entry()

    saved = await backfill_history_media([entry], bot_id=BOT_ID)

    assert saved == 1
    data = entry["base"]["segments"][0]["data"]
    assert data["media"]["sha"] == image_media.sha_of(PNG_BYTES)
    assert "url" not in data
