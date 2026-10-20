"""动图/表情包拆帧测试。

背景：视觉模型这条链路上，GIF 能不能被"看到"取决于上游实现——多数网关对
``image/gif`` 只读第一帧，个别直接 400。所以框架自己把动图拆成若干静态帧。
本文件锁住这条链路的五件事：

1. 拆帧本身：等间隔采样必含首尾、帧数受 ``max_frames`` 约束、静态图不拆、
   坏数据不抛；
2. 动图判定线索：``[动画表情]`` 标注 / ``.gif`` 后缀 / ``sub_type=1`` / 商城表情；
3. ``resolve_images`` 把动图段变成多个带 ``note`` 的 JPEG 帧条目，静态图行为不变；
4. 关掉开关时退回"单张原图"，不拆帧；
5. 组装侧：``note`` 渲染成一段说明文本，且**只出现一次**（模型要能看出这几帧同源）。
"""

from __future__ import annotations

import base64
import io

import pytest
from PIL import Image as PILImage

from app.agent.assembly.assembler import PromptRequest, place_images
from app.agent.media.animated import extract_frames, frame_indices, is_animated
from app.agent.media.image import (
    _animated_hint,
    build_user_content,
    collect_image_segments,
    count_images,
    resolve_images,
)
from app.domain.events import GroupMessageEvent, MessageSegment, UserInfo


def _gif_bytes(frames: int = 5, size: tuple[int, int] = (64, 64)) -> bytes:
    """造一张真动图：每帧一个明显不同的颜色，便于断言"取到的帧不是同一张"。"""
    colors = [(0, 0, 0), (220, 20, 20), (20, 220, 20), (20, 20, 220), (230, 230, 30),
              (200, 20, 200), (20, 200, 200), (120, 120, 120)]
    images = [PILImage.new("RGB", size, colors[i % len(colors)]) for i in range(frames)]
    buffer = io.BytesIO()
    images[0].save(buffer, format="GIF", save_all=True, append_images=images[1:], duration=60, loop=0)
    return buffer.getvalue()


def _approx(pixel, color, tol: int = 24) -> bool:
    """GIF 有调色板量化，颜色允许有偏差。"""
    return all(abs(int(a) - int(b)) <= tol for a, b in zip(pixel[:3], color[:3], strict=False))


def _png_bytes(size: tuple[int, int] = (32, 32)) -> bytes:
    buffer = io.BytesIO()
    PILImage.new("RGB", size, (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def _event(segments):
    return GroupMessageEvent(
        event_type="message_group",
        message_type="group",
        time=1788342260,
        user_id=20002,
        self_id=3569937952,
        message=[MessageSegment(t, d) for t, d in segments],
        user=UserInfo(user_id=20002, nickname="小明"),
        group=type("G", (), {"group_id": 778})(),
    )


def _b64_seg(data: bytes, *, summary: str = "[动画表情]", file: str = "") -> tuple[str, dict]:
    """base64:// 形态的图片段（不触网），并按 QQ 的实际上报形态打上动图线索。"""
    content = {"base64": f"base64://{base64.b64encode(data).decode()}"}
    if summary:
        content["summary"] = summary
    if file:
        content["file"] = file
    return ("image", content)


# ==================== 1. 拆帧本身 ====================


def test_frame_indices_include_first_and_last():
    assert frame_indices(30, 3) == [0, 15, 29]
    assert frame_indices(5, 5) == [0, 1, 2, 3, 4]
    assert frame_indices(2, 6) == [0, 1]      # 帧数不足时不重复取
    assert frame_indices(1, 6) == [0]
    assert frame_indices(0, 6) == [0]         # 脏数据按 1 帧处理


def test_extract_frames_returns_jpeg_frames():
    data = _gif_bytes(frames=5)
    assert is_animated(data)

    frames = extract_frames(data, max_frames=5, max_edge=64)

    assert len(frames) == 5
    for frame in frames:
        with PILImage.open(io.BytesIO(frame)) as im:
            assert im.format == "JPEG"          # 统一转 JPEG：动图帧直接给网关容易被拒
            assert max(im.size) <= 64


def test_extract_frames_respects_max_frames_and_keeps_ends():
    data = _gif_bytes(frames=8)

    frames = extract_frames(data, max_frames=3, max_edge=64)

    assert len(frames) == 3
    # 首帧与末帧必须分别来自第 0 帧 / 第 7 帧（等间隔采样，不是"只取前 3 帧"）
    with PILImage.open(io.BytesIO(frames[0])) as first:
        with PILImage.open(io.BytesIO(frames[-1])) as last:
            assert _approx(first.getpixel((1, 1)), (0, 0, 0))
            assert _approx(last.getpixel((1, 1)), (120, 120, 120))


def test_extract_frames_ignores_static_image():
    assert extract_frames(_png_bytes()) == []
    assert not is_animated(_png_bytes())


def test_extract_frames_survives_broken_data():
    assert extract_frames(b"not-an-image-at-all") == []
    assert extract_frames(b"") == []


def test_extract_frames_handles_transparent_gif():
    """透明帧要合成到白底：带 alpha 直接存 JPEG 会得到黑底或直接报错。"""
    frames = []
    for i in range(3):
        frame = PILImage.new("RGBA", (32, 32), (255, 0, 0, 0))  # 全透明底
        frame.paste((255, 0, 0, 255), (i * 8, 0, i * 8 + 6, 6))  # 一小块不透明，保证帧不重复
        frames.append(frame)
    buffer = io.BytesIO()
    frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:], duration=60, loop=0)

    result = extract_frames(buffer.getvalue(), max_frames=3, max_edge=32)

    assert len(result) == 3
    with PILImage.open(io.BytesIO(result[0])) as im:
        assert im.mode == "RGB"
        assert im.getpixel((20, 20)) == (255, 255, 255)   # 透明区 → 白底


# ==================== 2. 动图线索 ====================


def _seg(seg_type: str, data: dict):
    """OneBot 段的最小替身（type/data 两个属性，与 MessageSegment 同形）。"""
    import types as _types

    return _types.SimpleNamespace(type=seg_type, data=data)


@pytest.mark.parametrize(
    ("seg", "expected"),
    [
        (("image", {"summary": "[动画表情]"}), True),
        (("image", {"file": "abc.gif"}), True),
        (("image", {"url": "https://cdn.example/x.webp?term=2"}), True),
        (("image", {"sub_type": "1"}), True),
        (("mface", {"summary": "[表情]", "url": "https://cdn.example/m"}), True),
        (("image", {"file": "abc.jpg", "summary": "图片"}), False),
        (("image", {"url": "https://cdn.example/x.png"}), False),
    ],
)
def test_animated_hint(seg, expected):
    assert _animated_hint(_seg(seg[0], seg[1])) is expected


def test_probe_all_images_overrides_hint():
    assert _animated_hint(_seg("image", {"url": "https://cdn.example/x.png"}), probe_all=True) is True


def test_mface_counts_as_image():
    """商城表情（mface）必须算"有图"，否则只发表情包的消息会被当成空消息丢掉。"""
    event = _event([("mface", {"summary": "[表情]", "url": "https://cdn.example/m"})])

    assert count_images(event) == 1
    assert len(collect_image_segments(event)) == 1


# ==================== 3. resolve_images 的动图分支 ====================


async def test_resolve_images_splits_animated_gif():
    event = _event([_b64_seg(_gif_bytes(frames=6))])

    images = await resolve_images(event, config={"gif_max_frames": 4})

    assert len(images) == 4
    assert all(image["kind"] == "base64" for image in images)
    assert all(image["mime"] == "image/jpeg" for image in images)
    # 同一张动图的帧共用一个 note，渲染侧靠它拼出"同一来源"的说明
    notes = {image["note"] for image in images}
    assert len(notes) == 1
    assert "动图" in notes.pop()


async def test_resolve_images_keeps_static_image_single():
    event = _event([_b64_seg(_png_bytes(), summary="")])

    images = await resolve_images(event, config={})

    assert len(images) == 1
    assert "note" not in images[0]


async def test_resolve_images_without_gif_switch_returns_original():
    """关掉动图理解时行为退回老样子：一条图片条目，不拆帧。"""
    event = _event([_b64_seg(_gif_bytes(frames=6))])

    images = await resolve_images(event, config={"gif_understanding_enable": False})

    assert len(images) == 1
    assert images[0]["mime"] == "image/gif"


async def test_resolve_images_does_not_split_hinted_static_image():
    """线索是"看起来像动图"，真的不是动图时只能是一条静态图，不能变成帧。"""
    event = _event([_b64_seg(_png_bytes(), file="looks-like.gif")])

    images = await resolve_images(event, config={})

    assert len(images) == 1
    assert "note" not in images[0]


async def test_resolve_images_shares_frame_budget_across_gifs():
    """一条消息里多张动图共享帧数预算，避免 3 张动图各拆 6 帧把请求打爆。"""
    event = _event([
        _b64_seg(_gif_bytes(frames=6), summary="[动画表情]"),
        _b64_seg(_gif_bytes(frames=6), summary="[动画表情]"),
    ])

    images = await resolve_images(event, config={"gif_max_frames": 4})

    framed = [image for image in images if image.get("note")]
    assert len(framed) == 4          # 第一张用完整份预算
    assert len(images) == 5          # 第二张退回单张原图


# ==================== 4. 组装侧：note 与门控 ====================


def test_build_user_content_emits_note_once():
    images = [
        {"kind": "base64", "value": "AAA", "mime": "image/jpeg", "note": "动图 2 帧"},
        {"kind": "base64", "value": "BBB", "mime": "image/jpeg", "note": "动图 2 帧"},
    ]

    parts = build_user_content(images, text="看这个")

    assert parts[0] == {"type": "text", "text": "看这个"}
    assert parts[1] == {"type": "text", "text": "动图 2 帧"}
    assert [p["type"] for p in parts[2:]] == ["image_url", "image_url"]


def test_build_user_content_static_images_unchanged():
    parts = build_user_content([{"kind": "base64", "value": "AAA", "mime": "image/png"}], text="")

    assert parts == [
        {"type": "text", "text": "[用户发送了图片]"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ]


def test_build_user_content_drops_remote_url_entries():
    """远端地址永远不进请求体（历史直链会过期 → 网关拉不到 → 整轮 400）。"""
    assert build_user_content([{"kind": "url", "value": "https://cdn.example/a.png"}], text="") is None


def test_place_images_puts_frames_into_user_message():
    frames = [
        {"kind": "base64", "value": "AAA", "mime": "image/jpeg", "note": "【这是一张动图表情】"},
        {"kind": "base64", "value": "BBB", "mime": "image/jpeg", "note": "【这是一张动图表情】"},
    ]
    req = PromptRequest(
        runtime=None, event=_event([("text", {"text": "笑死"})]),
        user_text="笑死", user_images=frames, modalities=["text", "image"],
    )

    messages = place_images([{"role": "user", "content": "笑死"}], req)

    content = messages[0]["content"]
    assert isinstance(content, list)
    assert sum(1 for part in content if part["type"] == "image_url") == 2


def test_place_images_keeps_placeholder_for_text_only_model():
    frames = [{"kind": "base64", "value": "AAA", "mime": "image/jpeg", "note": "动图"}]
    req = PromptRequest(
        runtime=None, event=_event([("text", {"text": "笑死"})]),
        user_text="笑死", user_images=frames, modalities=["text"],
    )
    messages = [{"role": "user", "content": "笑死"}]

    assert place_images(messages, req) == [{"role": "user", "content": "笑死"}]
