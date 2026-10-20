"""会话图片的本地仓：**每账号一个 media 目录**，历史里只留一个指向。

为什么要这一层（线上真实故障）：QQ 图片直链自带 ``rkey``，几小时后失效。历史里若存着
直链，下一轮请求会把它原样发给网关 → 网关去拉 → 到期链接 403/404 → **整条请求 400**。
注意后果不是"少一张图"，而是整轮回复作废，而且那条历史还在，于是之后**每一轮**都失败一次。

所以口径改成：

1. **收到即落盘**（``data/llm/<bot_id>/media/<sha16>.<ext>``，按内容寻址、天然去重）；
2. 历史里**不存远端地址**，只留 ``{"sha", "mime", "bytes"}`` 这个指向；
3. 模型侧与前端侧**都读这同一个指向**——模型内联成 ``data:image/…;base64,…``
   （强制本地 base64，不依赖网关能否访问本机，也不依赖任何会过期的地址），
   前端走 ``/api/media/<sha>.<ext>``。两边指向同一个 sha，前端删图/换图就是改这个指向，
   模型下一轮自动跟着变。

两条硬规矩：

- **删除必须先删引用**：:func:`delete_media` 默认拒绝仍被任何会话引用的 sha，并返回
  "谁还在用它"的清单；要删就先在会话里把那张图移除/替换掉。
- **自动清理只碰无人引用的文件**：:func:`gc` 以"历史里出现过哪些 sha"为保护集，
  容量超限时也只从无人引用的里面淘汰（宁可留超限日志，也不悄悄删掉还在用的图）。
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import re
from collections.abc import Iterator
from typing import Any

from app.agent import bot_data_dir
from app.core.logger import logger
from app.modules import media_cache

#: 媒体目录名（挂在 ``data/llm/<bot_id>/`` 下，随账号隔离）
MEDIA_DIR_NAME = "media"

#: 指向里允许的 sha 形态（16 位小写十六进制）——同时用于挡目录穿越
SHA_RE = re.compile(r"^[0-9a-f]{16}$")

#: 会被当成「图片」处理的段类型。``mface`` 是 QQ 商城表情，data 里同样有
#: url/file；不认它等于表情包整类都看不到。
IMAGE_LIKE_SEGMENTS = ("image", "mface")

#: MIME ↔ 扩展名。文件名带扩展名只为人工排查方便；**真值永远由字节嗅探决定**
#: （OneBot 的 ``file`` 名经常没有后缀或后缀不靠谱）。
EXT_BY_MIME = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/bmp": "bmp",
}
MIME_BY_EXT = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
}

#: 未被引用的文件保留天数（<=0 表示不按时间淘汰）
DEFAULT_TTL_DAYS = 14
#: 每账号 media 目录的容量上限
DEFAULT_MAX_MB = 512
#: 单张内联进请求体的上限（超过就降采样；base64 会再膨胀 1/3，太大必被网关拒）
DEFAULT_INLINE_MAX_MB = 4
#: 内联前的长边上限（Pillow 缩图，原图仍留盘）
DEFAULT_INLINE_MAX_EDGE = 1568
_JPEG_QUALITY = 85
#: 写入过程中的半成品（``.part``）超过这个秒数就当垃圾清掉
_PART_TTL_SECONDS = 3600


def log():
    """带 ``[ImageMedia]`` 前缀的日志器（模块内统一出口）。"""
    return logger.add_info("ImageMedia")


# ==================== 段 / 条目形状 ====================


def seg_type(seg: Any) -> str:
    """取段类型（兼容 dict 与 ``MessageSegment`` 对象）。"""
    if isinstance(seg, dict):
        return str(seg.get("type", "") or "")
    return str(getattr(seg, "type", "") or "")


def seg_data(seg: Any) -> dict:
    """取段 data（兼容 dict 与 ``MessageSegment`` 对象）。"""
    if isinstance(seg, dict):
        data = seg.get("data")
    else:
        data = getattr(seg, "data", None)
    return data if isinstance(data, dict) else {}


def entry_segments(entry: Any) -> list:
    """历史条目里的段列表（结构化条目在 ``base.segments``，旧条目在顶层 ``segments``）。"""
    if isinstance(entry, dict):
        base = entry.get("base")
        if isinstance(base, dict) and isinstance(base.get("segments"), list):
            return base["segments"]
        segments = entry.get("segments")
        return segments if isinstance(segments, list) else []
    base = getattr(entry, "base", None)
    segments = getattr(base, "segments", None)
    if isinstance(segments, list):
        return segments
    segments = getattr(entry, "segments", None)
    return segments if isinstance(segments, list) else []


def segment_media(seg: Any) -> dict | None:
    """图片段里的本地指向（没有/不合法时返回 None）。"""
    media = seg_data(seg).get("media")
    if not isinstance(media, dict):
        return None
    return media if SHA_RE.match(str(media.get("sha", "") or "").strip().lower()) else None


def strip_remote_addresses(data: dict) -> None:
    """去掉会过期的远端地址（落盘成功后它们只剩误导作用，且是 400 的源头）。"""
    for key in ("url", "base64"):
        data.pop(key, None)
    file_value = str(data.get("file", "") or "").strip().lower()
    if file_value.startswith(("http://", "https://", "data:", "base64:")):
        data.pop("file", None)


# ==================== 字节 / MIME ====================


def sniff_mime(data: bytes) -> str:
    """按魔数嗅探图片格式；认不出返回空串（**不要瞎猜**，猜错就是网关 400）。"""
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
    return ""


def mime_from_name(name: str) -> str:
    """按文件扩展名猜 MIME（拿不到字节时的兜底）；不认识的扩展名返回空串。"""
    ext = str(name or "").rsplit(".", 1)[-1].lower()
    return MIME_BY_EXT.get(ext, "")


def sha_of(data: bytes) -> str:
    """内容寻址用的短片：同图必然同名，跨会话/跨账号自动去重。"""
    return hashlib.sha256(data).hexdigest()[:16]


def _known_mime(mime: Any) -> str:
    """把调用方声明的 MIME 归一化；只认我们支持的图片类型。"""
    text = str(mime or "").strip().lower().split(";", 1)[0].strip()
    if text == "image/jpg":
        text = "image/jpeg"
    return text if text in EXT_BY_MIME else ""


# ==================== 目录 / 落盘 ====================


def media_dir(bot_id: Any) -> str:
    """该账号的媒体目录路径（**不创建**）。"""
    return os.path.join(bot_data_dir(bot_id), MEDIA_DIR_NAME)


def ensure_dir(bot_id: Any) -> str:
    """该账号的媒体目录（不存在则创建）。"""
    directory = media_dir(bot_id)
    os.makedirs(directory, exist_ok=True)
    return directory


def path_of(bot_id: Any, sha: str) -> str | None:
    """sha → 磁盘路径（找不到返回 None）。

    目录里同名不同扩展名不可能出现（扩展名永远由字节嗅探决定），这里仍按前缀扫描，
    是为了兼容"人工丢进来的文件"。
    """
    sha = str(sha or "").strip().lower()
    if not SHA_RE.match(sha):
        return None
    directory = media_dir(bot_id)
    if not os.path.isdir(directory):
        return None
    prefix = f"{sha}."
    try:
        for name in os.listdir(directory):
            if name.startswith(prefix) and os.path.isfile(os.path.join(directory, name)):
                return os.path.join(directory, name)
    except OSError as e:  # noqa: BLE001 —— 目录临时不可读按"没有这张图"处理
        log().debug(f"扫描媒体目录失败: {e}")
    return None


def save_bytes(bot_id: Any, data: bytes, *, mime: str = "") -> dict | None:
    """字节 → 本地文件 + 指向；同内容重复调用直接命中（幂等）。

    返回 ``{"sha", "mime", "bytes"}``，可直接塞进段的 ``data["media"]``。
    MIME **永远按字节嗅探**：OneBot 声明的 MIME 与真实字节不一致时，网关会直接 400。
    """
    if not data:
        return None
    sha = sha_of(data)
    # MIME 一律按字节嗅探；认不出的格式只在调用方声明了已知图片类型时才接受
    # （存下去也发不出去：网关会以"不是图片"为由 400，不如现在就当没图）
    resolved = sniff_mime(data) or _known_mime(mime)
    if not resolved:
        log().warning(f"无法识别的图片格式（{len(data)} 字节），按无图处理")
        return None
    ext = EXT_BY_MIME.get(resolved, "bin")
    directory = ensure_dir(bot_id)
    path = os.path.join(directory, f"{sha}.{ext}")
    if not os.path.exists(path):
        tmp = f"{path}{media_cache.PART_SUFFIX}"
        try:
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        except OSError as e:  # noqa: BLE001 —— 落盘失败只降级，不能拖垮对话
            log().warning(f"图片落盘失败（按无图处理）: {e}")
            try:
                os.remove(tmp)
            except OSError:
                pass
            return None
    return {"sha": sha, "mime": resolved, "bytes": len(data)}


def read_bytes(bot_id: Any, sha: str, *, max_bytes: int = 0) -> bytes | None:
    """按指向读回原始字节（文件已丢/超限返回 None）。"""
    path = path_of(bot_id, sha)
    if not path:
        return None
    try:
        if max_bytes and os.path.getsize(path) > max_bytes:
            return None
        with open(path, "rb") as fh:
            return fh.read()
    except OSError as e:  # noqa: BLE001
        log().debug(f"读取本地图片失败 {sha}: {e}")
        return None


def downscale(data: bytes, *, max_edge: int = DEFAULT_INLINE_MAX_EDGE, quality: int = _JPEG_QUALITY):
    """超过长边上限时缩一张小的（透明图保 PNG，其余转 JPEG）。失败返回 None。"""
    try:
        from PIL import Image as PILImage
    except ImportError as e:  # pragma: no cover —— Pillow 缺席时整条降采样降级
        log().debug(f"Pillow 不可用，跳过图片降采样: {e}")
        return None
    try:
        with PILImage.open(io.BytesIO(data)) as im:
            im.seek(0)
            has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
            work = im.convert("RGBA" if has_alpha else "RGB")
            edge = max(64, int(max_edge or DEFAULT_INLINE_MAX_EDGE))
            if max(work.size) > edge:
                work.thumbnail((edge, edge), PILImage.LANCZOS)
            buffer = io.BytesIO()
            if has_alpha:
                work.save(buffer, format="PNG", optimize=True)
                return buffer.getvalue(), "image/png"
            work.save(buffer, format="JPEG", quality=min(95, max(30, int(quality))), optimize=True)
            return buffer.getvalue(), "image/jpeg"
    except Exception as e:  # noqa: BLE001 —— 解不开就当作"缩不了"
        log().debug(f"图片降采样失败（按原图处理）: {e}")
        return None


def inline_bytes(
    bot_id: Any,
    media: dict | None,
    *,
    max_mb: float = DEFAULT_INLINE_MAX_MB,
    max_edge: int = DEFAULT_INLINE_MAX_EDGE,
) -> tuple[bytes, str] | None:
    """指向 → 可内联的 ``(字节, MIME)``：超过 ``max_mb`` 先降采样，降不了返回 None。

    返回 None 的两层含义都指向同一个决定：**宁可这一张不发，也不发一个必然被网关拒的
    巨串**（那会连累整轮请求）。文件已丢同样返回 None，由调用方退化成占位。
    """
    sha = str((media or {}).get("sha", "") or "").strip().lower()
    if not SHA_RE.match(sha):
        return None
    data = read_bytes(bot_id, sha)
    if not data:
        return None
    limit = int(float(max_mb or 0) * 1024 * 1024)
    if limit > 0 and len(data) > limit:
        shrunk = downscale(data, max_edge=max_edge)
        if not shrunk:
            log().warning(f"图片过大且无法降采样，本轮不传给模型（{len(data) / 1024 / 1024:.1f}MB）")
            return None
        data = shrunk[0]
        if len(data) > limit:
            log().warning(f"图片降采样后仍超过内联上限，本轮不传给模型（{len(data) / 1024 / 1024:.1f}MB）")
            return None
    return data, sniff_mime(data)


def data_url(
    bot_id: Any,
    media: dict | None,
    *,
    max_mb: float = DEFAULT_INLINE_MAX_MB,
    max_edge: int = DEFAULT_INLINE_MAX_EDGE,
) -> str | None:
    """指向 → ``data:image/…;base64,…``（模型请求体里唯一的图片形态）。"""
    inline = inline_bytes(bot_id, media, max_mb=max_mb, max_edge=max_edge)
    if not inline:
        return None
    data, mime = inline
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


# ==================== 引用扫描 ====================


def _iter_messages(bot_id: Any) -> Iterator[tuple[str, str, int, dict]]:
    """遍历某账号全部归档消息 → ``(task_id, session_id, index, message)``。

    走存储层公开接口（``list_tasks`` / ``load_history``），不碰 SQL：JSON 与 SQLite
    两种后端都能用。GC 与"删除前查引用"都靠它，属于低频操作，多读一遍可接受。
    """
    from app.agent.storage import create_history_store

    store = None
    try:
        store = create_history_store(bot_id)
        for task in store.list_tasks():
            task_id = str(task.get("task_id", "") or "")
            if not task_id:
                continue
            data = store.load_history(task_id) or {}
            session_id = str(data.get("session_id", "") or "")
            for index, message in enumerate(data.get("messages") or []):
                if isinstance(message, dict):
                    yield task_id, session_id, index, message
    except Exception as e:  # noqa: BLE001 —— 扫描失败按"没有引用"处理（只影响清理力度）
        log().warning(f"扫描会话图片引用失败（{bot_id}）: {e}")
    finally:
        close = getattr(store, "close", None)
        if callable(close):
            try:
                close()
            except Exception as e:  # noqa: BLE001 —— 关连接失败只留痕，扫描结果已经拿到
                log().debug(f"关闭历史存储失败: {e}")


def scan_references(bot_id: Any) -> dict[str, list[dict]]:
    """``sha → [引用位置, …]``（引用位置 = ``{task_id, session_id, index, seg}``）。"""
    refs: dict[str, list[dict]] = {}
    for task_id, session_id, index, message in _iter_messages(bot_id):
        for seg_index, seg in enumerate(entry_segments(message)):
            media = segment_media(seg)
            if not media:
                continue
            sha = str(media.get("sha", "")).strip().lower()
            refs.setdefault(sha, []).append({
                "task_id": task_id,
                "session_id": session_id,
                "index": index,
                "seg": seg_index,
            })
    return refs


def references_of(bot_id: Any, sha: str) -> list[dict]:
    """某个 sha 当前被哪些会话条目引用。"""
    sha = str(sha or "").strip().lower()
    return scan_references(bot_id).get(sha, [])


def message_media_refs(entries: Any, message_id: Any) -> list[dict]:
    """某个消息 id 在给定历史里留下的本地图片指向（按段序）。

    供 ``expand_image`` 复用：**已经落过盘的消息不必再向 OneBot 要一次**，
    QQ 侧消息过期后也还能把图递给模型。
    """
    mid = str(message_id or "").strip()
    if not mid:
        return []
    refs: list[dict] = []
    for entry in entries or []:
        current = str((entry or {}).get("message_id", "") or "").strip() if isinstance(entry, dict) else ""
        if current != mid:
            continue
        for seg in entry_segments(entry):
            if seg_type(seg) not in IMAGE_LIKE_SEGMENTS:
                continue
            media = segment_media(seg)
            if media:
                refs.append(media)
    return refs


# ==================== 删除 / 清理 ====================


def delete_media(bot_id: Any, sha: str) -> dict:
    """删除一个本地图片文件；**仍被引用时拒绝**（先删引用再来）。

    返回 ``{"ok": bool, "reason": str, "references": [...]}``。前端把这个结果原样展示，
    用户就知道该先去哪条消息里移除那张图。
    """
    sha = str(sha or "").strip().lower()
    if not SHA_RE.match(sha):
        return {"ok": False, "reason": "非法的图片标识"}
    path = path_of(bot_id, sha)
    if not path:
        return {"ok": False, "reason": "图片不存在（可能已被清理）"}
    refs = references_of(bot_id, sha)
    if refs:
        return {
            "ok": False,
            "reason": f"仍有 {len(refs)} 处会话引用这张图，请先在这些消息里移除或替换",
            "references": refs,
        }
    try:
        size = os.path.getsize(path)
        os.remove(path)
    except OSError as e:  # noqa: BLE001
        return {"ok": False, "reason": f"删除失败: {e}"}
    log().info(f"本地图片已删除: {sha}（{media_cache.human_size(size)}）")
    return {"ok": True, "freed": size, "references": []}


def gc(
    bot_id: Any,
    *,
    ttl_days: float = DEFAULT_TTL_DAYS,
    max_mb: float = DEFAULT_MAX_MB,
) -> dict:
    """清理**无人引用**的过期/超量图片；被引用的文件永不自动删除。"""
    result = {"removed": 0, "freed": 0, "kept_referenced": 0, "bytes": 0, "over_cap": False}
    directory = media_dir(bot_id)
    if not os.path.isdir(directory):
        return result

    referenced = set(scan_references(bot_id))
    candidates: list[tuple[str, float, int]] = []
    total = 0
    try:
        names = os.listdir(directory)
    except OSError as e:  # noqa: BLE001
        log().warning(f"读取媒体目录失败: {e}")
        return result

    for name in names:
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        base = name.split(".", 1)[0].lower()
        try:
            mtime = os.path.getmtime(path)
            size = os.path.getsize(path)
        except OSError:
            continue
        if name.endswith(media_cache.PART_SUFFIX):
            # 半成品：只在明显过期时清掉，正常写入是"临时文件 + os.replace"
            if media_cache.is_fresh(path, int(_PART_TTL_SECONDS / 60)):
                continue
            candidates.append((path, mtime, size))
            continue
        total += size
        if base in referenced:
            result["kept_referenced"] += 1
            continue
        candidates.append((path, mtime, size))

    stale = media_cache.pick_stale(candidates, int(float(ttl_days or 0) * 24 * 60), int(float(max_mb or 0) * 1024 * 1024))
    for path in stale:
        try:
            size = os.path.getsize(path)
            os.remove(path)
            total -= size
            result["removed"] += 1
            result["freed"] += size
        except OSError as e:  # noqa: BLE001
            log().debug(f"清理图片失败 {path}: {e}")

    result["bytes"] = max(0, total)
    limit = int(float(max_mb or 0) * 1024 * 1024)
    result["over_cap"] = bool(limit > 0 and result["bytes"] > limit)
    if result["removed"]:
        log().info(
            f"媒体清理: 删除 {result['removed']} 张（{media_cache.human_size(result['freed'])}），"
            f"保留被引用 {result['kept_referenced']} 张"
        )
    if result["over_cap"]:
        log().warning(
            f"媒体目录仍超上限（{media_cache.human_size(result['bytes'])} > {max_mb}MB）："
            "剩余文件都还被会话引用，删图请走会话界面"
        )
    return result


def purge_unreferenced(bot_id: Any) -> dict:
    """删掉**当前没有任何会话引用**的图片（前端「清理未引用」按钮）。

    与 :func:`gc` 的区别：不看时间、不看容量，只看引用——用户点了清理就是"这些图我确实
    不要了"。被引用的文件一律保留：要删请先在会话里把那张图移除/替换。
    """
    result = {"removed": 0, "freed": 0, "kept_referenced": 0}
    directory = media_dir(bot_id)
    if not os.path.isdir(directory):
        return result
    referenced = set(scan_references(bot_id))
    try:
        names = os.listdir(directory)
    except OSError as e:  # noqa: BLE001
        log().warning(f"读取媒体目录失败: {e}")
        return result
    for name in names:
        path = os.path.join(directory, name)
        if not os.path.isfile(path) or name.endswith(media_cache.PART_SUFFIX):
            continue
        if name.split(".", 1)[0].lower() in referenced:
            result["kept_referenced"] += 1
            continue
        try:
            size = os.path.getsize(path)
            os.remove(path)
            result["removed"] += 1
            result["freed"] += size
        except OSError as e:  # noqa: BLE001
            log().debug(f"清理未引用图片失败 {path}: {e}")
    if result["removed"]:
        log().info(
            f"清理未引用图片: {result['removed']} 张"
            f"（{media_cache.human_size(result['freed'])}），保留被引用 {result['kept_referenced']} 张"
        )
    return result


def stats(bot_id: Any) -> dict:
    """媒体仓概况（前端「会话数据」页的占用统计用）。"""
    directory = media_dir(bot_id)
    items: list[dict] = []
    total = 0
    if os.path.isdir(directory):
        referenced = set(scan_references(bot_id))
        for name in sorted(os.listdir(directory)):
            path = os.path.join(directory, name)
            if not os.path.isfile(path) or name.endswith(media_cache.PART_SUFFIX):
                continue
            sha = name.split(".", 1)[0].lower()
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            total += size
            items.append({
                "sha": sha,
                "ext": name.rsplit(".", 1)[-1].lower(),
                "bytes": size,
                "mtime": int(os.path.getmtime(path)),
                "referenced": sha in referenced,
            })
    return {
        "dir": directory,
        "count": len(items),
        "bytes": total,
        "referenced": sum(1 for i in items if i["referenced"]),
        "unreferenced": sum(1 for i in items if not i["referenced"]),
        "items": items,
    }


__all__ = [
    "MEDIA_DIR_NAME",
    "SHA_RE",
    "IMAGE_LIKE_SEGMENTS",
    "EXT_BY_MIME",
    "MIME_BY_EXT",
    "DEFAULT_TTL_DAYS",
    "DEFAULT_MAX_MB",
    "DEFAULT_INLINE_MAX_MB",
    "DEFAULT_INLINE_MAX_EDGE",
    "seg_type",
    "seg_data",
    "entry_segments",
    "segment_media",
    "strip_remote_addresses",
    "sniff_mime",
    "mime_from_name",
    "sha_of",
    "media_dir",
    "ensure_dir",
    "path_of",
    "save_bytes",
    "read_bytes",
    "downscale",
    "inline_bytes",
    "data_url",
    "scan_references",
    "references_of",
    "message_media_refs",
    "delete_media",
    "gc",
    "purge_unreferenced",
    "stats",
]
