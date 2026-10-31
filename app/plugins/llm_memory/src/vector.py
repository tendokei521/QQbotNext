"""向量工具（memory v3）：float32 打包 / 解包、余弦相似度、L2 归一化。

存储层把 embedding 以 little-endian float32 的 BLOB 落库（见 §4.1 ``memories.embedding``），
本模块是唯一的编解码入口：``store.set_embedding`` 写、召回层读时解包。
零依赖（只用标准库 ``struct``），几千~几万条记忆的纯 Python 余弦足够。
"""

from __future__ import annotations

import math
import struct

# float32 little-endian 的打包前缀：``<{n}f``
_LE_FLOAT32 = "<{}f"


def pack(vector: list[float]) -> bytes:
    """把 float 列表打包成 little-endian float32 字节串（空列表 → ``b""``）。"""
    data = [float(x) for x in vector]
    if not data:
        return b""
    return struct.pack(_LE_FLOAT32.format(len(data)), *data)


def unpack(blob: bytes | None) -> list[float]:
    """把 BLOB 解包成 float 列表；空 / 损坏 / 非 4 字节对齐 → ``[]``（不抛异常）。"""
    if not blob:
        return []
    try:
        raw = bytes(blob)
    except (TypeError, ValueError):
        return []
    if not raw or len(raw) % 4:
        return []
    count = len(raw) // 4
    try:
        return list(struct.unpack(_LE_FLOAT32.format(count), raw))
    except struct.error:
        return []


def cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度（范围 [-1, 1]）；长度不等 / 空 / 零向量 → 0.0。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b, strict=False):
        fx = float(x)
        fy = float(y)
        dot += fx * fy
        norm_a += fx * fx
        norm_b += fy * fy
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    value = dot / (math.sqrt(norm_a) * math.sqrt(norm_b))
    # 浮点误差可能越界，钳到 [-1, 1]
    return max(-1.0, min(1.0, value))


def norm(vector: list[float]) -> list[float]:
    """L2 归一化；零向量 / 空列表原样返回（不做除零）。"""
    data = [float(x) for x in vector]
    if not data:
        return data
    total = math.sqrt(sum(x * x for x in data))
    if total <= 0.0:
        return data
    return [x / total for x in data]
