"""多模态图片内容构建（OneBot 图片段 → 本地落盘 → provider 可吃的 base64 图片块）。

背景：图片此前在 ``MessageEvent.text`` 处就被丢弃，历史/背景渲染成 ``[图片]``，
LLM 完全看不到图片内容。

**落盘优先（2026-10 改造）**：QQ 图片直链带 ``rkey``，几小时后失效；历史里存着直链
→ 下一轮请求把它发给网关 → 网关拉不到 → **整条请求 400**（不是少一张图，而是整轮作废，
而且那条历史一直在，于是之后每轮都失败一次）。所以口径改成：

- 图片一进框架就落成本地文件（见 :mod:`app.agent.media.store`，每账号一个 ``media/``）；
- 请求体里**只发本地字节**（``data:image/…;base64,…``），远端地址一律不再出现在
  请求里，也不写进历史（过期地址留在那只会误导排查）；
- 拿不到字节就不发这一张，正文照发（绝不退回"把直链丢给网关"那条死路）。

其余设计约束：

- **只处理本轮触发的那条消息**。群聊历史背景（``get_chat_history`` / 预历史）仍是纯文本占位；
- **有大小上限**。超出 ``max_image_bytes`` 的图片跳过并留日志；超过内联上限先降采样；
- **只在「看起来是动图」时才拆帧**（拆帧是纯计算，见 ``app/llm/animated.py``）；
  动图原始字节同样先落盘，历史里照样有指向。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import re
from collections.abc import Iterable
from typing import Any

from app.agent.media import store as image_media
from app.agent.media.animated import DEFAULT_FRAME_MAX_EDGE
from app.agent.media.animated import DEFAULT_MAX_FRAMES as DEFAULT_GIF_MAX_FRAMES
from app.agent.media.store import (
    DEFAULT_INLINE_MAX_EDGE,
    DEFAULT_INLINE_MAX_MB,
    IMAGE_LIKE_SEGMENTS,
    entry_segments,
)
from app.agent.media.store import (
    mime_from_name as _mime_from_name,
)
from app.agent.media.store import (
    seg_data as _seg_data,
)
from app.agent.media.store import (
    seg_type as _seg_type,
)
from app.agent.media.store import (
    sniff_mime as _sniff_mime,
)
from app.core.logger import logger

# 单张图片字节上限：base64 后约 1.33 倍，10MB 原图 ≈ 13MB 请求体，多数网关会直接拒
DEFAULT_MAX_IMAGE_BYTES = 10 * 1024 * 1024

_DATA_URL_RE = re.compile(r"^data:image/([a-z0-9.+-]+);base64,(.+)$", re.IGNORECASE | re.DOTALL)

#: 动图的**快速线索**（命中才去下字节看 n_frames）。
#: 真正的判定永远是 Pillow 的 ``n_frames > 1``（见 animated.py），这里只负责
#: 把「显然不是动图」的静态图挡在下载之外，避免每张图都多拉一次流量。
_ANIMATED_EXTS = ("gif", "webp", "apng")
_ANIMATED_MARKERS = ("动画表情", "动图", "animated")

#: 下载超时（秒）：QQ 图片 CDN 通常在 1s 内返回，15s 已是兜底
_ANIMATED_FETCH_TIMEOUT = 15.0


def _cfg(config: Any, key: str, default: Any) -> Any:
    """从配置对象/字典读一个键（AgentConfig 与 dict 都支持 ``get``，读失败回默认）。"""
    if config is None:
        return default
    try:
        value = config.get(key, default)
    except Exception as e:  # noqa: BLE001 —— 配置读失败不该拖垮图片解析
        logger.debug(f"[Image] 读取配置 {key} 失败（用默认值）: {e}")
        return default
    return default if value is None else value


def _log():
    return logger.add_info("Image")


def _as_int(value: Any) -> int | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def inline_budget(config: Any = None) -> tuple[float, int]:
    """内联进请求体的 ``(单张 MB 上限, 长边像素上限)``（与落盘上限分开的两把尺子）。"""
    try:
        max_mb = float(_cfg(config, "image_inline_max_mb", DEFAULT_INLINE_MAX_MB))
    except (TypeError, ValueError):
        max_mb = DEFAULT_INLINE_MAX_MB
    try:
        max_edge = int(_cfg(config, "image_inline_max_edge", DEFAULT_INLINE_MAX_EDGE))
    except (TypeError, ValueError):
        max_edge = DEFAULT_INLINE_MAX_EDGE
    if max_mb <= 0:
        max_mb = DEFAULT_INLINE_MAX_MB
    if max_edge <= 0:
        max_edge = DEFAULT_INLINE_MAX_EDGE
    return max_mb, max_edge


def cache_enabled(config: Any = None) -> bool:
    """是否把图片落盘（关掉等于回到"历史图片只能用一次"的旧行为）。"""
    return bool(_cfg(config, "image_cache_enable", True))


def count_images(event: Any) -> int:
    """本轮消息里的图片段数量（不校验可用性，仅用于日志/门控）。

    含 ``mface``（QQ 商城表情）：表情包也算「这条消息有图」，否则只发表情包的消息
    会被当成纯文本空消息丢掉。
    """
    return sum(
        1 for seg in (getattr(event, "message", None) or []) if _seg_type(seg) in IMAGE_LIKE_SEGMENTS
    )


def collect_image_segments(event: Any) -> list[Any]:
    """收集图片段（image + mface）；事件没有 message 字段时返回空列表。"""
    return [
        seg for seg in (getattr(event, "message", None) or []) if _seg_type(seg) in IMAGE_LIKE_SEGMENTS
    ]


def max_image_bytes(config: Any = None) -> int:
    """从配置读单张图片体积上限（``image_max_mb``），非法值回退默认。"""
    try:
        raw = (config or {}).get("image_max_mb", None)
        value = float(raw)
    except (TypeError, ValueError, AttributeError):
        return DEFAULT_MAX_IMAGE_BYTES
    if value <= 0:
        return DEFAULT_MAX_IMAGE_BYTES
    return int(min(value, 50) * 1024 * 1024)


# ==================== 段 → 本地字节 ====================


async def persist_segment(
    seg: Any,
    *,
    bot_id: Any = "",
    bot: Any = None,
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
) -> dict | None:
    """图片段 → 落盘 + 返回本地指向（``{"sha", "mime", "bytes"}``）。

    段里已经有指向时直接复用（幂等，不重复下载）。取字节的所有形态
    （data URL / ``base64://`` / http 直链 / ``get_image`` 兜底）都在 :func:`_segment_bytes`，
    这里只负责"取到就落盘"。
    """
    existing = image_media.segment_media(seg)
    if existing:
        return existing
    data, mime = await _segment_bytes(seg, bot=bot, max_bytes=max_bytes)
    if not data:
        return None
    return image_media.save_bytes(bot_id, data, mime=mime)


def _mutable_data(seg: Any) -> dict | None:
    """取段里**可写**的 data 字典（dict 段与 ``MessageSegment`` 对象都支持）。

    拿到的是段自己的那份 dict（不是拷贝）：写进去的 ``media`` 会跟着段走，
    所以"上游落过盘"这件事对后续所有读取方都可见（``_segments_as_dicts`` 复制时带上）。
    """
    if isinstance(seg, dict):
        data = seg.get("data")
        if not isinstance(data, dict):
            data = {}
            seg["data"] = data
        return data
    data = getattr(seg, "data", None)
    if isinstance(data, dict):
        return data
    try:
        seg.data = {}
        return seg.data
    except Exception as e:  # noqa: BLE001 —— 只读段对象写不了，放弃（调用方按未落盘处理）
        logger.debug(f"[image] 189: 已忽略异常: {e}")
        return None


async def persist_images(
    segments: Iterable[Any],
    *,
    bot_id: Any = "",
    bot: Any = None,
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
    timeout: float = 15.0,
) -> int:
    """把一批消息段里的图片就地落盘（写 ``data["media"]`` 并抹掉远端地址）。

    就地改写传入的段（调用方随后把同一批段写进历史），返回成功落盘的张数。
    失败的段保持原样但没有 ``media`` —— 渲染侧会退化成 ``[图片#id]`` 占位，
    模型仍可用 ``expand_image`` 按消息 id 再试一次。

    幂等：段里已有指向就直接跳过，所以"pipeline 先落一次、chat 再落一次"不会重复下载。
    """
    targets = [
        seg for seg in (segments or [])
        if _seg_type(seg) in IMAGE_LIKE_SEGMENTS and not image_media.segment_media(seg)
    ]
    if not targets:
        return 0

    async def _one(seg: Any) -> bool:
        try:
            media = await asyncio.wait_for(
                persist_segment(seg, bot_id=bot_id, bot=bot, max_bytes=max_bytes),
                timeout=max(1.0, float(timeout or 15.0)),
            )
        except asyncio.TimeoutError:
            _log().warning("图片下载超时，本条图片按占位处理")
            return False
        except Exception as e:  # noqa: BLE001 —— 单张失败不该影响其余图片与本轮对话
            _log().warning(f"图片落盘失败，本条图片按占位处理: {e}")
            return False
        if not media:
            return False
        data = _mutable_data(seg)
        if data is None:
            return False
        data["media"] = dict(media)
        image_media.strip_remote_addresses(data)
        return True

    results = await asyncio.gather(*(_one(seg) for seg in targets))
    return sum(1 for ok in results if ok)


async def resolve_images(
    event: Any = None,
    *,
    bot: Any = None,
    bot_id: Any = "",
    max_images: int = 4,
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
    config: Any = None,
    segments: Iterable[Any] | None = None,
) -> list[dict]:
    """把本轮消息里的图片解析成 ``[{"kind": "base64", "value": ..., "mime": ..., "media": ...}]``。

    **只发本地字节**：段里没有指向时现场落盘（``expand_image`` 取回的历史消息就是这种），
    落盘失败就跳过这张图并留日志——绝不把会过期的直链丢给网关。

    动图（GIF / 动态 WebP / 表情包）会被拆成若干静态帧，每个帧条目带同一个
    ``note``，由 :func:`build_user_content` 渲染成一句说明——模型才知道「这是同一张动图的
    连续帧」，而不是六张互不相干的图。

    Args:
        event: 消息事件（只用到 ``message`` 字段）；传了 ``segments`` 时不再读它。
        bot: 当前账号连接，用于 ``get_image`` 兜底换直链。
        bot_id: 落盘目录所属账号。
        max_images: **静态图**数量上限；动图帧另有 ``gif_max_frames`` 预算。
        max_bytes: 单张（或单个动图原始字节）体积上限。
        config: Agent 配置（读 ``gif_*`` / 内联预算开关）；缺省时按内置默认值。
        segments: 直接给一批段（``chat`` 会传"已经落过盘"的那批，避免重复下载）。

    解析不到的图片会被跳过（并记一条 warning），不会阻断整轮请求。
    """
    images: list[dict] = []
    limit = max(1, int(max_images or 1))
    gif_enable = bool(_cfg(config, "gif_understanding_enable", True))
    frame_budget = max(1, int(_cfg(config, "gif_max_frames", DEFAULT_GIF_MAX_FRAMES) or DEFAULT_GIF_MAX_FRAMES))
    max_edge = int(_cfg(config, "gif_frame_max_edge", DEFAULT_FRAME_MAX_EDGE) or DEFAULT_FRAME_MAX_EDGE)
    probe_all = bool(_cfg(config, "gif_probe_all_images", False))
    inline_max_mb, inline_max_edge = inline_budget(config)
    static_used = 0
    warned_limit = False

    source = list(segments) if segments is not None else collect_image_segments(event)
    for seg in source:
        media = image_media.segment_media(seg)
        if not media:
            media = await persist_segment(seg, bot_id=bot_id, bot=bot, max_bytes=max_bytes)
        if not media:
            continue
        # 动图优先：命中线索才下字节确认，确认是动图就拆帧（帧走独立的帧数预算，
        # 不吃静态图的 max_images 配额——一张表情包不该挤掉同一轮的 4 张静态图）
        if gif_enable and frame_budget > 0 and _animated_hint(seg, probe_all=probe_all):
            frames = await _frames_from_media(
                bot_id, media, max_frames=frame_budget, max_edge=max_edge
            )
            if frames:
                images.extend(frames)
                if len(frames) > 1:  # 真的拆成了多帧，扣预算
                    frame_budget -= len(frames)
                    _log().debug(f"动图拆帧：{len(frames)} 帧（剩余帧预算 {frame_budget}）")
                continue
        if static_used >= limit:
            if not warned_limit:
                _log().warning(f"图片数量超过上限 {max_images}，后续图片未传给模型")
                warned_limit = True
            continue
        entry = _inline_entry(bot_id, media, max_mb=inline_max_mb, max_edge=inline_max_edge)
        if entry:
            images.append(entry)
            static_used += 1
    return images


def _inline_entry(bot_id: Any, media: dict, *, max_mb: float, max_edge: int) -> dict | None:
    """本地指向 → 请求用的 base64 条目（超限先降采样，降不了就跳过这一张）。"""
    inline = image_media.inline_bytes(bot_id, media, max_mb=max_mb, max_edge=max_edge)
    if not inline:
        return None
    data, mime = inline
    return {
        "kind": "base64",
        "value": base64.b64encode(data).decode("ascii"),
        "mime": mime,
        "media": dict(media),
    }


def _animated_hint(seg: Any, *, probe_all: bool = False) -> bool:
    """段是否「看起来是动图」——命中才值得下载字节去确认 ``n_frames``。

    判定线索来自 NapCat/OneBot 的实际上报形态：
    - 文件名/URL 带 .gif / .webp / .apng（QQ 表情包常见 ``xxx.gif``）；
    - ``summary`` 是 ``[动画表情]``（QQ 客户端本身就是这么标动图的）；
    - ``mface``（商城表情）基本都是动的；
    - ``sub_type == "1"``：NapCat 用它标表情图片。
    这些线索**只用于省流量**，真的动图与否由 Pillow 的 ``n_frames`` 说了算。
    """
    if probe_all:
        return True
    data = _seg_data(seg)
    if _seg_type(seg) == "mface":
        return True
    if str(data.get("sub_type", "") or "").strip() == "1":
        return True
    for key in ("file", "url", "summary", "path"):
        value = str(data.get(key, "") or "").strip().lower()
        if not value:
            continue
        if any(marker in value for marker in _ANIMATED_MARKERS):
            return True
        ext = value.rsplit(".", 1)[-1].split("?", 1)[0].split("&", 1)[0]
        if ext in _ANIMATED_EXTS:
            return True
    return False


async def _frames_from_media(
    bot_id: Any, media: dict, *, max_frames: int, max_edge: int
) -> list[dict] | None:
    """本地图片 → 动图帧条目（不是动图/拆帧失败返回 None，由调用方按静态图处理）。

    字节来自本地仓：拆帧是纯计算，不需要再下载一次。
    """
    from app.agent.media.animated import extract_frames

    data = image_media.read_bytes(bot_id, str(media.get("sha", "")))
    if not data:
        return None
    # 拆帧是 CPU 活（动图解码），必须离开事件循环
    frames = await asyncio.to_thread(extract_frames, data, max_frames=max_frames, max_edge=max_edge)
    if not frames:
        return None
    note = f"【这是一张动图表情，下面 {len(frames)} 张图是它按时间顺序拆出的连续帧】"
    return [
        {
            "kind": "base64",
            "value": base64.b64encode(frame).decode("ascii"),
            "mime": "image/jpeg",
            "note": note,
        }
        for frame in frames
    ]


async def _segment_bytes(seg: Any, *, bot: Any, max_bytes: int) -> tuple[bytes | None, str]:
    """把图片段取成原始字节（data URL / base64:// / http 直链 / get_image 兜底）。"""
    data = _seg_data(seg)
    file_value = str(data.get("file", "") or "").strip()

    # 1) data:image/...;base64,xxx
    for candidate in (str(data.get("base64", "") or "").strip(), file_value):
        match = _DATA_URL_RE.match(candidate)
        if not match:
            continue
        payload = match.group(2)
        if len(payload) * 0.75 > max_bytes:
            _log().warning(f"图片超过体积上限，已跳过（{len(payload) * 0.75 / 1024 / 1024:.1f}MB）")
            return None, ""
        try:
            return base64.b64decode(payload), f"image/{match.group(1).lower()}"
        except (binascii.Error, ValueError) as e:
            _log().warning(f"data URL 解码失败: {e}")
            continue

    # 2) base64://xxx（OneBot 常用形态）
    for candidate in (str(data.get("base64", "") or "").strip(), file_value):
        lowered = candidate.lower()
        if not lowered.startswith("base64:"):
            continue
        raw = candidate[len("base64://"):] if lowered.startswith("base64://") else candidate[len("base64:"):]
        if len(raw) * 0.75 > max_bytes:
            _log().warning(f"图片超过体积上限，已跳过（{len(raw) * 0.75 / 1024 / 1024:.1f}MB）")
            return None, ""
        try:
            decoded = base64.b64decode(raw)
        except (binascii.Error, ValueError) as e:
            _log().warning(f"base64 图片解码失败: {e}")
            continue
        # MIME 按魔数嗅探：file 名经常缺失/后缀不靠谱，声明错的 MIME 会让网关 400
        return decoded, _sniff_mime(decoded)

    # 3) http(s) 直链
    for candidate in (str(data.get("url", "") or "").strip(), file_value):
        if candidate.lower().startswith(("http://", "https://")):
            return await _download_bytes(candidate, max_bytes=max_bytes)

    # 4) 让 OneBot 把 file 换成可访问 URL
    if bot is not None and file_value:
        try:
            resp = await bot.get_image(file_value)
        except Exception as e:  # noqa: BLE001 —— 取不到就降级，不能拖垮本轮对话
            _log().warning(f"get_image 失败: {e}")
            return None, ""
        payload = (resp or {}).get("data", {}) or {}
        for key in ("url", "file"):
            candidate = str(payload.get(key, "") or "").strip()
            if candidate.lower().startswith(("http://", "https://")):
                return await _download_bytes(candidate, max_bytes=max_bytes)
        _log().warning(f"get_image 未返回可用链接，图片未传给模型: {file_value}")
    return None, ""


async def _download_bytes(url: str, *, max_bytes: int, timeout: float = _ANIMATED_FETCH_TIMEOUT) -> tuple[bytes | None, str]:
    """下载图片字节；超时、超限、非 200 都返回 ``(None, "")`` 并留日志。"""
    import aiohttp

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    _log().warning(f"图片下载失败（HTTP {resp.status}）")
                    return None, ""
                declared = int(resp.headers.get("Content-Length") or 0)
                if declared > max_bytes:
                    _log().warning(f"图片超过体积上限，已跳过（{declared / 1024 / 1024:.1f}MB）")
                    return None, ""
                buffer = bytearray()
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    buffer.extend(chunk)
                    if len(buffer) > max_bytes:
                        _log().warning(f"图片超过体积上限，已跳过（>{max_bytes / 1024 / 1024:.0f}MB）")
                        return None, ""
                data = bytes(buffer)
                return data, _sniff_mime(data)
    except Exception as e:  # noqa: BLE001 —— 网络问题一律降级为"这张图不发"
        _log().warning(f"图片下载异常: {e}")
        return None, ""


def image_url_from_bytes(data: bytes, *, mime: str = "", name: str = "") -> str | None:
    """本地字节 → data URL（供后续「图片转述」等能力复用；超限返回 None）。"""
    if not data:
        return None
    if len(data) > DEFAULT_MAX_IMAGE_BYTES:
        return None
    resolved_mime = str(mime or "").strip() or _sniff_mime(data) or _mime_from_name(name)
    if not resolved_mime:
        return None  # 认不出格式：宁可不发，也不要一个 MIME 空/错的图块
    try:
        encoded = base64.b64encode(data).decode("ascii")
    except (binascii.Error, ValueError):
        return None
    return f"data:{resolved_mime};base64,{encoded}"


def build_user_content(images: Iterable[dict], *, text: str = "") -> list[dict] | None:
    """把已解析的图片 + 文本组合成 OpenAI 风格 content 块数组。

    条目可带 ``note``（如动图拆帧的「以下 N 张是同一张动图的连续帧」）：同一组 note
    只在**第一次**出现时插一段说明文本，让模型知道后面这几张图属于同一件事。
    没有任何可用图片时返回 None，调用方保持原有的纯字符串 content 行为。

    只认 ``kind="base64"``：远端 URL 一律丢弃（历史里存直链 → 过期 → 网关拉不到 →
    整轮 400，这正是这次改造要堵死的路）。真需要发图请先落盘再内联。
    """
    parts: list[dict] = []
    if text.strip():
        parts.append({"type": "text", "text": text})
    last_note = ""
    for image in images or []:
        if not isinstance(image, dict):
            continue
        kind = str(image.get("kind", "")).lower()
        value = str(image.get("value", "") or "").strip()
        if not value or kind != "base64":
            continue
        note = str(image.get("note", "") or "").strip()
        if note and note != last_note:
            parts.append({"type": "text", "text": note})
            last_note = note
        mime = str(image.get("mime", "") or "image/png").strip() or "image/png"
        parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{value}"}})
    if not any(p.get("type") == "image_url" for p in parts):
        return None
    # 纯图片（无正文）时补一句引导，避免 user 消息只有图块
    if not text.strip():
        parts.insert(0, {"type": "text", "text": "[用户发送了图片]"})
    return parts


# ==================== 会话历史里的图片 ====================


def history_image_segments(entry: Any) -> list[Any]:
    """历史条目里的图片段（``base.segments`` 里的 ``image`` / ``mface``）。

    兼容两种形状：结构化条目（``{"base": {"segments": [...]}}`` / ``HistoryEntry``）
    与旧条目（``{"segments": [...]}``）。
    """
    return [seg for seg in entry_segments(entry) if _seg_type(seg) in IMAGE_LIKE_SEGMENTS]


def history_image_parts(
    entry: Any,
    *,
    bot_id: Any = "",
    max_images: int = 4,
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
    max_mb: float | None = None,
    max_edge: int | None = None,
) -> list[dict]:
    """历史条目的图片段 → ``image_url`` 内容块（**只认本地指向，绝不发远端地址**）。

    「远端地址会过期 → 网关拉不到 → 整条请求 400」是本功能存在的理由，所以这里
    只接受段里的 ``data.media``（本地 sha 指向）：文件在就内联 base64，
    文件不在就返回空列表，由渲染侧退化成 ``[图片#id]`` 占位（模型可用 ``expand_image`` 取回）。

    ``max_bytes`` 是落盘上限（只是防止读进一个异常巨大的文件），真正决定"能不能内联"
    的是 ``max_mb`` / ``max_edge`` 这对内联预算。
    """
    parts: list[dict] = []
    limit = max(1, int(max_images or 1))
    inline_mb = DEFAULT_INLINE_MAX_MB if max_mb is None else max_mb
    inline_edge = DEFAULT_INLINE_MAX_EDGE if max_edge is None else max_edge
    for seg in history_image_segments(entry):
        if len(parts) >= limit:
            break
        media = image_media.segment_media(seg)
        if not media:
            continue
        url = image_media.data_url(
            bot_id, media, max_mb=inline_mb, max_edge=inline_edge
        )
        if url:
            parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts


async def backfill_history_media(
    entries: Iterable[Any],
    *,
    bot_id: Any = "",
    bot: Any = None,
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
    max_entries: int = 4,
    timeout: float = 5.0,
) -> int:
    """给"只有远端直链、没有本地指向"的历史条目补一次落盘（改造前的老数据）。

    由新到旧、最多 ``max_entries`` 张、总耗时不超过 ``timeout`` 秒：历史渲染发生在每轮
    请求前，不能因为回填把一轮对话拖成十几秒。**就地把指向写回条目**（传进来的必须是
    会话里的真实条目，而不是 ``get_history`` 的深拷贝），失败只留 debug 日志——
    链接已经过期的图确实救不回来，降级成 ``[图片#id]`` 占位即可。
    """
    budget = max(0, int(max_entries or 0))
    if budget <= 0:
        return 0
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(1.0, float(timeout or 5.0))
    done = 0
    for entry in reversed(list(entries or [])):
        for seg in history_image_segments(entry):
            if image_media.segment_media(seg):
                continue
            if budget <= 0 or loop.time() >= deadline:
                return done
            budget -= 1
            remaining = max(0.5, deadline - loop.time())
            try:
                media = await asyncio.wait_for(
                    persist_segment(seg, bot_id=bot_id, bot=bot, max_bytes=max_bytes),
                    timeout=remaining,
                )
            except Exception as e:  # noqa: BLE001 —— 回填是增强，失败一律忽略
                _log().debug(f"历史图片回填失败（已忽略）: {e}")
                media = None
            if not media:
                continue
            data = seg.get("data") if isinstance(seg, dict) else None
            if not isinstance(data, dict):
                continue
            data["media"] = dict(media)
            image_media.strip_remote_addresses(data)
            done += 1
    return done


__all__ = [
    "DEFAULT_MAX_IMAGE_BYTES",
    "IMAGE_LIKE_SEGMENTS",
    "backfill_history_media",
    "build_user_content",
    "cache_enabled",
    "collect_image_segments",
    "count_images",
    "history_image_parts",
    "history_image_segments",
    "image_url_from_bytes",
    "inline_budget",
    "max_image_bytes",
    "persist_images",
    "persist_segment",
    "resolve_images",
]
