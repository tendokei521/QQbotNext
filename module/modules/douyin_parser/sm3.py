"""SM3 哈希（GB/T 32905-2016）纯 Python 实现。

抖音 ``a_bogus`` 签名需要 SM3。上游实现依赖第三方 ``gmssl``，本插件自带实现，
避免为解析功能引入新依赖（依赖清单以 ``pyproject.toml`` 为唯一事实源）。

标准测试向量见 ``tests/test_douyin_sm3.py``。
"""

from __future__ import annotations

import struct

__all__ = ["sm3_digest", "sm3_hex", "sm3_bytes_to_list"]

# 初始值 IV
_IV = (
    0x7380166F, 0x4914B2B9, 0x172442D7, 0xDA8A0600,
    0xA96F30BC, 0x163138AA, 0xE38DEE4D, 0xB0FB0E4E,
)

_MASK = 0xFFFFFFFF
# 压缩函数常量：0~15 轮用 T0，16~63 轮用 T1
_T0 = 0x79CC4519
_T1 = 0x7A879D8A


def _rotl(value: int, shift: int) -> int:
    """32 位循环左移（``shift`` 自动对 32 取模）。"""
    shift %= 32
    if shift == 0:
        return value & _MASK
    return ((value << shift) | (value >> (32 - shift))) & _MASK


def _p0(x: int) -> int:
    return (x ^ _rotl(x, 9) ^ _rotl(x, 17)) & _MASK


def _p1(x: int) -> int:
    return (x ^ _rotl(x, 15) ^ _rotl(x, 23)) & _MASK


def _ff(x: int, y: int, z: int, j: int) -> int:
    """布尔函数 FF：前 16 轮异或，其后取多数。"""
    if j < 16:
        return x ^ y ^ z
    return (x & y) | (x & z) | (y & z)


def _gg(x: int, y: int, z: int, j: int) -> int:
    """布尔函数 GG：前 16 轮异或，其后按位选择。"""
    if j < 16:
        return x ^ y ^ z
    return (x & y) | ((~x & _MASK) & z)


def _compress(state: tuple, block: bytes) -> tuple:
    """压缩函数 CF：一个 512 位分组与 8 字状态。"""
    w = list(struct.unpack(">16I", block))
    for j in range(16, 68):
        w.append(
            _p1(w[j - 16] ^ w[j - 9] ^ _rotl(w[j - 3], 15))
            ^ _rotl(w[j - 13], 7)
            ^ w[j - 6]
        )
    w1 = [w[j] ^ w[j + 4] for j in range(64)]

    a, b, c, d, e, f, g, h = state
    for j in range(64):
        t = _T0 if j < 16 else _T1
        ss1 = _rotl((_rotl(a, 12) + e + _rotl(t, j)) & _MASK, 7)
        ss2 = ss1 ^ _rotl(a, 12)
        tt1 = (_ff(a, b, c, j) + d + ss2 + w1[j]) & _MASK
        tt2 = (_gg(e, f, g, j) + h + ss1 + w[j]) & _MASK
        d = c
        c = _rotl(b, 9)
        b = a
        a = tt1
        h = g
        g = _rotl(f, 19)
        f = e
        e = _p0(tt2)

    return (
        a ^ state[0], b ^ state[1], c ^ state[2], d ^ state[3],
        e ^ state[4], f ^ state[5], g ^ state[6], h ^ state[7],
    )


def _pad(data: bytes) -> bytes:
    """填充：附加 ``0x80``、若干 ``0x00``、64 位大端比特长度。"""
    bit_len = len(data) * 8
    zeros = (56 - (len(data) + 1) % 64) % 64
    return data + b"\x80" + b"\x00" * zeros + struct.pack(">Q", bit_len)


def sm3_digest(data: bytes) -> bytes:
    """计算 SM3 摘要，返回 32 字节。"""
    state = _IV
    padded = _pad(bytes(data))
    for offset in range(0, len(padded), 64):
        state = _compress(state, padded[offset:offset + 64])
    return struct.pack(">8I", *state)


def sm3_hex(data: bytes) -> str:
    """计算 SM3 摘要并返回小写十六进制字符串（对齐 gmssl ``sm3_hash`` 的返回）。"""
    return sm3_digest(data).hex()


def sm3_bytes_to_list(data: bytes) -> list:
    """字节转整数列表（对齐 gmssl ``func.bytes_to_list``，便于逐行对照上游）。"""
    return list(bytes(data))
