"""多模态图片内容构建（OneBot 图片段 → provider 可吃的图片块）。

背景：图片此前在 ``MessageEvent.text`` 处就被丢弃，历史/背景渲染成 ``[图片]``，
LLM 完全看不到图片内容。本模块负责把「本轮触发消息」里的图片段转成
OpenAI 风格的 ``{"type": "image_url", ...}`` 内容块；Anthropic / Gemini 的
格式差异由各自 provider 的原生化逻辑处理（见 providers/anthropic.py、gemini.py）。

设计约束：
- **只处理本轮触发的那条消息**。群聊历史背景（``get_chat_history`` / 预历史）仍然
  是纯文本占位，否则十张历史图片就是十个图块，token 直接爆掉；
- **必须能拿到可用 URL 才算数**。``file`` 往往是 QQ 内部文件名（``xxx.image``），
  直接塞进 API 只会换来一个 400，所以优先 ``url`` → ``base64`` → data/http 形态的 ``file``；
- **有大小上限**。超出 ``max_image_bytes`` 的图片跳过并留日志，避免单张巨图拖垮请求。
"""

from __future__ import annotations

import base64
import binascii
import re
from typing import Any, Iterable

from app.core.logger import logger

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
    if _seg_type(seg) != "image":
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
    """本轮消息里的图片段数量（不校验可用性，仅用于日志/门控）。"""
    return sum(1 for seg in (getattr(event, "message", None) or []) if _seg_type(seg) == "image")


def collect_image_segments(event: Any) -> list[Any]:
    """收集图片段；事件没有 message 字段（主动消息/测试替身）时返回空列表。"""
    return [seg for seg in (getattr(event, "message", None) or []) if _seg_type(seg) == "image"]


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
) -> list[dict]:
    """把本轮消息里的图片解析为 ``[{"kind": "url"|"base64", "value": ...}]``。

    解析不到的图片会被跳过（并记一条 warning），不会阻断整轮请求。
    """
    images: list[dict] = []
    for seg in collect_image_segments(event):
        if len(images) >= max(1, int(max_images or 1)):
            _log().warning(f"图片数量超过上限 {max_images}，后续图片未传给模型")
            break
        resolved = await _resolve_one(seg, bot=bot, max_bytes=max_bytes)
        if resolved:
            images.append(resolved)
    return images


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
    return {"kind": "base64", "value": payload, "mime": mime}


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

    没有任何可用图片时返回 None，调用方保持原有的纯字符串 content 行为。
    """
    parts: list[dict] = []
    if text.strip():
        parts.append({"type": "text", "text": text})
    for image in images or []:
        if not isinstance(image, dict):
            continue
        kind = str(image.get("kind", "")).lower()
        value = str(image.get("value", "") or "").strip()
        if not value:
            continue
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
