"""多模态图片内容构建（OneBot 图片段 → provider 可吃的图片块）。

背景：图片此前在 ``MessageEvent.text`` 处就被丢弃，历史/背景渲染成 ``[图片]``，
LLM 完全看不到图片内容。本模块负责把「本轮触发消息」里的图片段转成
OpenAI 风格的 ``{"type": "image_url", ...}`` 内容块；Anthropic / Gemini 的
格式差异由各自 provider 的原生化逻辑处理（见 providers/anthropic.py、gemini.py）。

动图（GIF / 动态 WebP / QQ 表情包）在这里被拆成若干静态帧后并入同一组图片块：
视觉模型这条路上「动图只看得到首帧」是上游实现决定的，框架自己拆帧才可控。
拆帧是纯计算，见 ``app/llm/animated.py``。

设计约束：
- **只处理本轮触发的那条消息**。群聊历史背景（``get_chat_history`` / 预历史）仍然
  是纯文本占位，否则十张历史图片就是十个图块，token 直接爆掉；
- **必须能拿到可用 URL 才算数**。``file`` 往往是 QQ 内部文件名（``xxx.image``），
  直接塞进 API 只会换来一个 400，所以优先 ``url`` → ``base64`` → data/http 形态的 ``file``；
- **有大小上限**。超出 ``max_image_bytes`` 的图片跳过并留日志，避免单张巨图拖垮请求；
- **只在「看起来是动图」时才下载**。静态图走原有的 URL 直传，不额外拉一遍流量。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import re
from typing import Any, Iterable

from app.core.logger import logger
from app.llm.animated import DEFAULT_FRAME_MAX_EDGE
from app.llm.animated import DEFAULT_MAX_FRAMES as DEFAULT_GIF_MAX_FRAMES

# 单张图片字节上限：base64 后约 1.33 倍，10MB 原图 ≈ 13MB 请求体，多数网关会直接拒
DEFAULT_MAX_IMAGE_BYTES = 10 * 1024 * 1024
# base64 体积估算：4 个字符表示 3 字节
_B64_EXPANSION = 4 / 3

_DATA_URL_RE = re.compile(r"^data:image/([a-z0-9.+-]+);base64,(.+)$", re.IGNORECASE | re.DOTALL)
# 从 file:///path/a.jpg 里抠扩展名 → MIME
_EXT_MIME = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
}

#: 会被当成「图片」处理的段类型。``mface`` 是 QQ 商城表情，data 里同样有
#: url/file；不认它等于表情包整类都看不到。
IMAGE_LIKE_SEGMENTS = ("image", "mface")

#: 动图的**快速线索**（命中才去下字节看 n_frames）。
#: 真正的判定永远是 Pillow 的 ``n_frames > 1``（见 animated.py），这里只负责
#: 把「显然不是动图」的静态图挡在下载之外，避免每张图都多拉一次流量。
_ANIMATED_EXTS = ("gif", "webp", "apng")
_ANIMATED_MARKERS = ("动画表情", "动图", "animated")

#: 下载动图时的超时（秒）：QQ 图片 CDN 通常在 1s 内返回，15s 已是兜底
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


def _seg_type(seg: Any) -> str:
    return seg.type if hasattr(seg, "type") else (seg.get("type", "") if isinstance(seg, dict) else "")


def _seg_data(seg: Any) -> dict:
    if hasattr(seg, "data"):
        return seg.data or {}
    if isinstance(seg, dict):
        return seg.get("data", {}) or {}
    return {}


def _as_int(value: Any) -> int | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _sniff_mime(data: bytes) -> str:
    """按魔数嗅探图片格式：OneBot 给的 ``file`` 名经常没有扩展名。"""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    return "image/png"


def _mime_from_name(name: str) -> str:
    ext = str(name or "").rsplit(".", 1)[-1].lower()
    return _EXT_MIME.get(ext, "image/png")


def _is_understandable_image(seg: Any) -> bool:
    """该图片段是否可能是可用 URL / base64（否则不值得额外发起 get_image 拉取）。"""
    if _seg_type(seg) not in IMAGE_LIKE_SEGMENTS:
        return False
    data = _seg_data(seg)
    for key in ("url", "base64", "file"):
        value = str(data.get(key, "") or "").strip()
        if not value:
            continue
        lowered = value.lower()
        if lowered.startswith(("http://", "https://", "data:image/", "base64://")):
            return True
        if key in ("url", "base64") or value.startswith("base64://"):
            return True
    return False


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


async def resolve_images(
    event: Any,
    *,
    bot: Any = None,
    max_images: int = 4,
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
    config: Any = None,
) -> list[dict]:
    """把本轮消息里的图片解析为 ``[{"kind": "url"|"base64", "value": ...}]``。

    动图（GIF / 动态 WebP / 表情包）会被拆成若干静态帧，每个帧条目带同一个
    ``note``，由 ``build_user_content`` 渲染成一句说明——模型才知道「这是同一张动图
    的连续帧」，而不是六张互不相干的图。

    Args:
        event: 消息事件（只用到 ``message`` 字段）。
        bot: 当前账号连接，用于 ``get_image`` 兜底换直链。
        max_images: **静态图**数量上限；动图帧另有 ``gif_max_frames`` 预算。
        max_bytes: 单张（或单个动图原始字节）体积上限。
        config: Agent 配置（读 ``gif_*`` 开关）；缺省时按内置默认值。

    解析不到的图片会被跳过（并记一条 warning），不会阻断整轮请求。
    """
    images: list[dict] = []
    limit = max(1, int(max_images or 1))
    gif_enable = bool(_cfg(config, "gif_understanding_enable", True))
    frame_budget = max(1, int(_cfg(config, "gif_max_frames", DEFAULT_GIF_MAX_FRAMES) or DEFAULT_GIF_MAX_FRAMES))
    max_edge = int(_cfg(config, "gif_frame_max_edge", DEFAULT_FRAME_MAX_EDGE) or DEFAULT_FRAME_MAX_EDGE)
    probe_all = bool(_cfg(config, "gif_probe_all_images", False))
    static_used = 0
    warned_limit = False

    for seg in collect_image_segments(event):
        # 动图优先：命中线索才下字节确认，确认是动图就拆帧（帧走独立的帧数预算，
        # 不吃静态图的 max_images 配额——一张表情包不该挤掉同一轮的 4 张静态图）
        if gif_enable and frame_budget > 0 and _animated_hint(seg, probe_all=probe_all):
            entries = await _resolve_animated(
                seg, bot=bot, max_bytes=max_bytes,
                max_frames=frame_budget, max_edge=max_edge,
            )
            if entries:
                images.extend(entries)
                if len(entries) > 1:  # 真的拆成了多帧，扣预算
                    frame_budget -= len(entries)
                    _log().debug(f"动图拆帧：{len(entries)} 帧（剩余帧预算 {frame_budget}）")
                continue
        if static_used >= limit:
            if not warned_limit:
                _log().warning(f"图片数量超过上限 {max_images}，后续图片未传给模型")
                warned_limit = True
            continue
        resolved = await _resolve_one(seg, bot=bot, max_bytes=max_bytes)
        if resolved:
            images.append(resolved)
            static_used += 1
    return images


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


async def _resolve_animated(
    seg: Any, *, bot: Any, max_bytes: int, max_frames: int, max_edge: int
) -> list[dict] | None:
    """动图段 → 帧条目列表。

    Returns:
        帧条目列表（多帧或退化成单帧）；拿不到字节时返回 ``None``（交给常规路径）。
        **注意**：只要字节拿到了就一定返回条目——确认「其实不是动图」时用已下载的
        字节直接构造单图条目，避免同一次请求里重复下载同一张图。
    """
    from app.llm.animated import extract_frames

    data, mime = await _segment_bytes(seg, bot=bot, max_bytes=max_bytes)
    if not data:
        return None

    # 拆帧是 CPU 活（动图解码），必须离开事件循环
    frames = await asyncio.to_thread(extract_frames, data, max_frames=max_frames, max_edge=max_edge)
    if not frames:
        return [_entry_from_bytes(data, mime)]

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


def _entry_from_bytes(data: bytes, mime: str) -> dict | None:
    """原始字节 → 单图片条目（超过体积上限返回 None）。"""
    if not data:
        return None
    if len(data) > DEFAULT_MAX_IMAGE_BYTES:
        _log().warning(f"图片超过体积上限，已跳过（{len(data) / 1024 / 1024:.1f}MB）")
        return None
    return {"kind": "base64", "value": base64.b64encode(data).decode("ascii"), "mime": mime or "image/png"}


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
            _log().warning(f"get_image 失败（动图按静态图处理）: {e}")
            return None, ""
        payload = (resp or {}).get("data", {}) or {}
        for key in ("url", "file"):
            candidate = str(payload.get(key, "") or "").strip()
            if candidate.lower().startswith(("http://", "https://")):
                return await _download_bytes(candidate, max_bytes=max_bytes)
    return None, ""


async def _download_bytes(url: str, *, max_bytes: int, timeout: float = _ANIMATED_FETCH_TIMEOUT) -> tuple[bytes | None, str]:
    """下载图片/动图字节；超时、超限、非 200 都返回 ``(None, "")`` 并留日志。"""
    import aiohttp

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    _log().warning(f"动图下载失败（HTTP {resp.status}）")
                    return None, ""
                declared = int(resp.headers.get("Content-Length") or 0)
                if declared > max_bytes:
                    _log().warning(f"动图超过体积上限，已跳过（{declared / 1024 / 1024:.1f}MB）")
                    return None, ""
                buffer = bytearray()
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    buffer.extend(chunk)
                    if len(buffer) > max_bytes:
                        _log().warning(f"动图超过体积上限，已跳过（>{max_bytes / 1024 / 1024:.0f}MB）")
                        return None, ""
                data = bytes(buffer)
                return data, _sniff_mime(data)
    except Exception as e:  # noqa: BLE001 —— 网络问题一律降级为「当静态图处理」
        _log().warning(f"动图下载异常（按静态图处理）: {e}")
        return None, ""


async def _resolve_one(seg: Any, *, bot: Any, max_bytes: int) -> dict | None:
    data = _seg_data(seg)
    file_value = str(data.get("file", "") or "").strip()
    mime = _mime_from_name(file_value)

    # 1) 直链：优先 url，其次 file 本身就是 http(s) 的情况
    for candidate in (str(data.get("url", "") or "").strip(), file_value):
        if candidate.lower().startswith(("http://", "https://")):
            return {"kind": "url", "value": candidate}

    # 2) 已经是 data URL：按体积估算决定是否直传
    for candidate in (str(data.get("base64", "") or "").strip(), file_value):
        match = _DATA_URL_RE.match(candidate)
        if match:
            payload = match.group(2)
            if len(payload) * 0.75 > max_bytes:
                _log().warning(f"图片超过体积上限，已跳过（{len(payload) * 0.75 / 1024 / 1024:.1f}MB）")
                return None
            return {"kind": "url", "value": candidate}

    # 3) base64:// 形态（OneBot 常用）
    raw_b64 = ""
    for candidate in (str(data.get("base64", "") or "").strip(), file_value):
        if candidate.lower().startswith("base64://"):
            raw_b64 = candidate[len("base64://"):]
            break
        if candidate.lower().startswith("base64:"):
            raw_b64 = candidate[len("base64:"):]
            break
    if raw_b64:
        return _from_base64(raw_b64, mime=mime, max_bytes=max_bytes)

    # 4) 兜底：让 OneBot 把 file 转成可访问 URL（get_image），异常不影响主流程
    if bot is not None and file_value:
        try:
            resp = await bot.get_image(file_value)
        except Exception as e:  # noqa: BLE001 —— 图片解析失败必须降级，不能拖垮本轮对话
            _log().warning(f"get_image 失败，改用占位: {e}")
            return None
        payload_data = (resp or {}).get("data", {}) or {}
        url = str(payload_data.get("url", "") or "").strip()
        if url.lower().startswith(("http://", "https://")):
            return {"kind": "url", "value": url}
        converted = str(payload_data.get("file", "") or "").strip()
        if converted.lower().startswith(("http://", "https://")):
            return {"kind": "url", "value": converted}
        _log().warning(f"get_image 未返回可用链接，图片未传给模型: {file_value}")
    return None


def _from_base64(raw_b64: str, *, mime: str, max_bytes: int) -> dict | None:
    payload = raw_b64.strip()
    if not payload:
        return None
    # 带 data: 前缀的裸串直接按 data URL 用
    match = _DATA_URL_RE.match(payload)
    if match:
        mime = f"image/{match.group(1).lower()}"
        payload = match.group(2)
    if len(payload) * 0.75 > max_bytes:
        _log().warning(f"图片超过体积上限，已跳过（{len(payload) * 0.75 / 1024 / 1024:.1f}MB）")
        return None
    return {"kind": "base64", "value": payload, "mime": _sniff_b64_mime(payload, mime)}


def _sniff_b64_mime(payload: str, declared: str = "") -> str:
    """按魔数校正 base64 载荷的 MIME。

    OneBot 的 ``file`` 名经常缺失或后缀不靠谱（``xxx.image``），声明的 MIME 与真实
    字节不一致时网关会直接 400；这里解出前 64 字节嗅探一次，认不出就保留声明值。
    """
    try:
        head = base64.b64decode(payload[:96] + "=" * (-len(payload[:96]) % 4))
    except (binascii.Error, ValueError):
        return declared or "image/png"
    sniffed = _sniff_mime(head)
    if sniffed == "image/png" and declared:
        # _sniff_mime 的兜底值不是证据，别用它覆盖明确的声明
        return declared
    return sniffed


def image_url_from_bytes(data: bytes, *, mime: str = "", name: str = "") -> str | None:
    """本地字节 → data URL（供后续「图片转述」等能力复用；超限返回 None）。"""
    if not data:
        return None
    if len(data) > DEFAULT_MAX_IMAGE_BYTES:
        return None
    resolved_mime = str(mime or "").strip() or _sniff_mime(data) or _mime_from_name(name)
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
        if not value:
            continue
        note = str(image.get("note", "") or "").strip()
        if note and note != last_note:
            parts.append({"type": "text", "text": note})
            last_note = note
        if kind == "base64":
            mime = str(image.get("mime", "") or "image/png").strip() or "image/png"
            parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{value}"}})
        elif kind == "url":
            parts.append({"type": "image_url", "image_url": {"url": value}})
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
    base: Any
    if isinstance(entry, dict):
        base = entry.get("base")
        if not isinstance(base, dict):
            return [s for s in (entry.get("segments") or []) if _seg_type(s) in IMAGE_LIKE_SEGMENTS]
        segments = base.get("segments") or []
    else:
        base = getattr(entry, "base", None)
        segments = getattr(base, "segments", None) or getattr(entry, "segments", None) or []
    return [s for s in segments if _seg_type(s) in IMAGE_LIKE_SEGMENTS]


def history_image_parts(
    entry: Any,
    *,
    max_images: int = 4,
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
) -> list[dict]:
    """历史条目的图片段 → ``image_url`` 内容块（**纯本地解析，不发起网络请求**）。

    为什么只做本地解析：历史渲染发生在每轮请求前，逐图 ``get_image`` 会把一次
    对话变成一串 API 调用；而且 QQ 直链会过期，历史图片本就该"能用就用"。
    因此只接受段里已经带地址的形态（``url`` / ``data:image/…`` / ``base64://``）；
    只有 ``file``（QQ 内部文件名，需要 ``get_image`` 换直链）的段保持
    ``[图片#id]`` 占位，交给 ``expand_image`` 按需取回。
    """
    parts: list[dict] = []
    limit = max(1, int(max_images or 1))
    for seg in history_image_segments(entry):
        if len(parts) >= limit:
            break
        item = _local_image_entry(seg, max_bytes=max_bytes)
        if not item:
            continue
        if item["kind"] == "base64":
            mime = str(item.get("mime", "") or "image/png")
            url = f"data:{mime};base64,{item['value']}"
        else:
            url = str(item.get("value", "") or "")
        if url:
            parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts


def _local_image_entry(seg: Any, *, max_bytes: int) -> dict | None:
    """图片段 → 条目（仅本地可解析形态；不下载、不调 OneBot）。

    与 ``_resolve_one`` 的差别只在最后一步：这里**没有** ``get_image`` 兜底。
    """
    data = _seg_data(seg)
    file_value = str(data.get("file", "") or "").strip()

    # 1) 直链（含 data URL：OpenAI 兼容接口两者都吃）
    for candidate in (str(data.get("url", "") or "").strip(), file_value):
        lowered = candidate.lower()
        if lowered.startswith(("http://", "https://", "data:image/")):
            return {"kind": "url", "value": candidate}

    # 2) base64:// 形态（OneBot 常用）→ 解出字节后按魔数校正 MIME
    raw_b64 = ""
    for candidate in (str(data.get("base64", "") or "").strip(), file_value):
        lowered = candidate.lower()
        if lowered.startswith("base64://"):
            raw_b64 = candidate[len("base64://"):]
            break
        if lowered.startswith("base64:"):
            raw_b64 = candidate[len("base64:"):]
            break
    if raw_b64:
        return _from_base64(raw_b64, mime=_mime_from_name(file_value), max_bytes=max_bytes)

    # 3) 只剩 file 名（QQ 内部文件名）：本地拿不到地址，保持占位
    return None
