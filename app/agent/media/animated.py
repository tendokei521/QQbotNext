"""动图拆帧：GIF / 动态 WebP → 若干张静态 JPEG 帧。

背景：QQ 的表情包与动图以 ``image`` 段上报（``sub_type=1``、``summary="[动画表情]"``），
而视觉模型这条链路上「动图能不能被看到」完全取决于上游实现——多数 OpenAI 兼容网关
对 ``image/gif`` 只读第一帧，个别直接 400。框架自己把动图拆成若干静态帧，
「动图能看到」这件事才不依赖网关的脸色。

设计约束（都是踩过的坑）：
- **只拆动图**：``n_frames <= 1`` 一律返回空列表，让调用方保持原有「单图直传」路径；
- **帧数有上限**：均匀采样且必含首尾帧——只取前 N 帧会漏掉后半段的表情变化；
- **帧要小**：长边缩到 ``max_edge`` 再转 JPEG。动图单帧常比静态图大得多，
  6 帧原尺寸就够把请求体打爆；
- **失败不抛**：Pillow 缺席、数据损坏、格式不认识，一律返回空列表由调用方降级。

这里只做「字节 → 帧字节」的纯计算，不碰网络、不碰配置（配置由调用方读取后传入）。
"""

from __future__ import annotations

import io

from app.core.logger import logger

#: 单张动图最多拆几帧。6 帧足以表达「开头→变化→结尾」，再多只是烧 token。
DEFAULT_MAX_FRAMES = 6
#: 帧长边上限（像素）。512 已经是视觉模型「看得清又不贵」的平衡点。
DEFAULT_FRAME_MAX_EDGE = 512
#: JPEG 质量。82 在 512px 上是肉眼无损档，体积约为 95 的一半。
DEFAULT_FRAME_QUALITY = 82


def _log():
    return logger.add_info("Animated")


def frame_indices(total_frames: int, max_frames: int) -> list[int]:
    """在 ``total_frames`` 里等间隔挑出帧下标，必含首帧与末帧。

    Args:
        total_frames: 动图总帧数（``<= 0`` 视为 1）。
        max_frames: 最多要几帧（``<= 0`` 视为 1）。

    Returns:
        升序、去重的下标列表；帧数不足时返回全部下标。

    Examples:
        >>> frame_indices(30, 3)
        [0, 15, 29]
        >>> frame_indices(2, 6)
        [0, 1]
    """
    total = max(1, int(total_frames or 1))
    want = max(1, min(int(max_frames or 1), total))
    if want == 1:
        return [0]
    # 端点对齐的线性插值：保证首帧（通常是表情包"定型"的那一帧）一定在。
    # 用整数四舍五入而不是 round()——round() 是银行家舍入（round(14.5) == 14），
    # 采样点会偏向前半段。
    span = want - 1
    return sorted({(i * (total - 1) + span // 2) // span for i in range(want)})


def _flatten_to_rgb(frame):
    """把任意模式的帧转成可存 JPEG 的 RGB；透明区合成到白底。"""
    from PIL import Image as PILImage

    if frame.mode == "RGB":
        return frame
    if frame.mode in ("RGBA", "LA") or (frame.mode == "P" and "transparency" in frame.info):
        rgba = frame.convert("RGBA")
        canvas = PILImage.new("RGB", rgba.size, (255, 255, 255))
        canvas.paste(rgba, mask=rgba.split()[-1])
        return canvas
    return frame.convert("RGB")


def extract_frames(
    data: bytes,
    *,
    max_frames: int = DEFAULT_MAX_FRAMES,
    max_edge: int = DEFAULT_FRAME_MAX_EDGE,
    quality: int = DEFAULT_FRAME_QUALITY,
) -> list[bytes]:
    """动图字节 → 若干 JPEG 帧字节。

    Args:
        data: 动图原始字节（GIF / 动态 WebP / APNG 等 Pillow 认得的格式）。
        max_frames: 最多拆几帧。
        max_edge: 每帧长边上限（像素）。
        quality: JPEG 质量。

    Returns:
        帧的 JPEG 字节列表；**非动图**返回空列表（调用方据此走单图路径），
        解码失败或 Pillow 不可用同样返回空列表（不抛，降级由调用方决定）。
    """
    if not data:
        return []
    try:
        from PIL import Image as PILImage
    except ImportError as e:  # pragma: no cover —— 依赖缺失时整条动图链路降级
        _log().warning(f"Pillow 不可用，动图按静态图处理: {e}")
        return []

    frames: list[bytes] = []
    try:
        with PILImage.open(io.BytesIO(data)) as im:
            total = int(getattr(im, "n_frames", 1) or 1)
            if total <= 1:
                return []
            edge = max(1, int(max_edge or DEFAULT_FRAME_MAX_EDGE))
            q = min(95, max(30, int(quality or DEFAULT_FRAME_QUALITY)))
            for index in frame_indices(total, max_frames):
                im.seek(index)
                # 每帧都要 copy：seek 之后 im 会被下一次 seek 就地改写
                frame = _flatten_to_rgb(im.copy())
                frame.thumbnail((edge, edge), PILImage.LANCZOS)
                buffer = io.BytesIO()
                frame.save(buffer, "JPEG", quality=q, optimize=True)
                frames.append(buffer.getvalue())
    except Exception as e:  # noqa: BLE001 —— 坏图/畸形动图不该炸掉整轮对话
        _log().warning(f"动图拆帧失败，按静态图处理: {e}")
        return []
    return frames


def is_animated(data: bytes) -> bool:
    """字节是否为动图（``n_frames > 1``）；无法解码时返回 ``False``。"""
    if not data:
        return False
    try:
        from PIL import Image as PILImage
    except ImportError:  # pragma: no cover
        return False
    try:
        with PILImage.open(io.BytesIO(data)) as im:
            return int(getattr(im, "n_frames", 1) or 1) > 1
    except Exception as e:  # noqa: BLE001
        _log().debug(f"动图判定失败（按静态图处理）: {e}")
        return False
