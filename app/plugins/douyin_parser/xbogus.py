"""抖音 ``X-Bogus`` 签名（纯 Python，仅用标准库）。

``a_bogus`` 之前的旧签名方案，部分接口仍在用。本插件以 ``a_bogus`` 为主，
``X-Bogus`` 作为失败回退（与上游一致）。

算法结构：UA 指纹 → 双重 MD5 → 时间戳/常量拼装 → 奇偶拆位 → RC4 → 自定义 base64。
常量（字符表、UA 密钥）与公开逆向实现一致，来源与许可见插件 README。
"""

from __future__ import annotations

import base64
import hashlib
import time

__all__ = ["XBogus"]

_CHARSET = "Dkdpgh4ZKsQB80/Mfvw36XI1R25-WUAlEi7NLboqYTOPuzmFjJnryx9HVGcaStCe="
_UA_KEY = b"\x00\x01\x0c"
_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0"
)
#: 常量盐值（上游固定值）
_CONST_SALT = 536919696


class XBogus:
    """生成抖音 API 请求所需的 ``X-Bogus`` 签名参数。"""

    def __init__(self, user_agent: str | None = None) -> None:
        # 十六进制字符 → 半字节值映射：'0'-'9' → 0-9，'a'-'f' → 10-15
        self._char_map = [None] * 128
        for i in range(10):
            self._char_map[48 + i] = i
        for i in range(6):
            self._char_map[97 + i] = 10 + i
        self.user_agent = user_agent or _DEFAULT_UA

    # ---------- 哈希工具 ----------

    def _md5_str_to_array(self, md5_str: str) -> list:
        """十六进制 MD5 串 → 字节数组；非 32 位十六进制串按原字符码返回。"""
        if len(md5_str) > 32:
            return [ord(c) for c in md5_str]
        arr = []
        for i in range(0, len(md5_str), 2):
            high = self._char_map[ord(md5_str[i])]
            low = self._char_map[ord(md5_str[i + 1])]
            arr.append((high << 4) | low)
        return arr

    @staticmethod
    def _md5(data) -> str:
        """MD5：str 按字符码取字节，list 直接当字节数组。"""
        if isinstance(data, str):
            arr = [ord(c) for c in data]
        elif isinstance(data, list):
            arr = data
        else:
            raise ValueError("Invalid input type")
        return hashlib.md5(bytes(arr)).hexdigest()

    def _md5_encrypt(self, url_path: str) -> list:
        """URL 路径的双重 MD5（先哈希原串，再哈希其十六进制字节）。"""
        first = self._md5(url_path)
        second = self._md5(self._md5_str_to_array(first))
        return self._md5_str_to_array(second)

    # ---------- 编码工具 ----------

    @staticmethod
    def _encoding_conversion(a, b, c, e, d, t, f, r, n, o, i, _x, x, u, s, m, v, h, p) -> str:
        """按算法规定的交错顺序拼字节串（参数顺序是算法关键，不可调整）。"""
        y = [a]
        y.append(int(i))
        y.extend([b, _x, c, x, e, u, d, s, t, m, f, v, r, h, n, p, o])
        return bytes(y).decode("ISO-8859-1")

    @staticmethod
    def _encoding_conversion2(a: int, b: int, c: str) -> str:
        return chr(a) + chr(b) + c

    @staticmethod
    def _rc4_encrypt(key: bytes, data: bytes) -> bytearray:
        s = list(range(256))
        j = 0
        for i in range(256):
            j = (j + s[i] + key[i % len(key)]) % 256
            s[i], s[j] = s[j], s[i]

        i = j = 0
        encrypted = bytearray()
        for byte in data:
            i = (i + 1) % 256
            j = (j + s[i]) % 256
            s[i], s[j] = s[j], s[i]
            encrypted.append(byte ^ s[(s[i] + s[j]) % 256])
        return encrypted

    def _calc(self, a1: int, a2: int, a3: int) -> str:
        """三个字节 → 4 个自定义 base64 字符。"""
        x = ((a1 & 255) << 16) | ((a2 & 255) << 8) | a3
        return (
            _CHARSET[(x >> 18) & 63]
            + _CHARSET[(x >> 12) & 63]
            + _CHARSET[(x >> 6) & 63]
            + _CHARSET[x & 63]
        )

    # ---------- 主接口 ----------

    def get_xbogus(self, url_path: str) -> tuple:
        """生成 ``X-Bogus``。

        Args:
            url_path: 完整 URL 或已编码的查询串

        Returns:
            ``(拼好签名的参数字符串, X-Bogus 值, 使用的 User-Agent)``
        """
        rc4_encrypted = self._rc4_encrypt(_UA_KEY, self.user_agent.encode("ISO-8859-1"))
        array1 = self._md5_str_to_array(
            self._md5(base64.b64encode(rc4_encrypted).decode("ISO-8859-1"))
        )
        # 空串 MD5 的二次哈希：固定盐
        array2 = self._md5_str_to_array(
            self._md5(self._md5_str_to_array("d41d8cd98f00b204e9800998ecf8427e"))
        )
        url_path_array = self._md5_encrypt(url_path)

        timer = int(time.time())
        new_array = [
            64, 0.00390625, 1, 12,
            url_path_array[14], url_path_array[15],
            array2[14], array2[15],
            array1[14], array1[15],
            (timer >> 24) & 255, (timer >> 16) & 255,
            (timer >> 8) & 255, timer & 255,
            (_CONST_SALT >> 24) & 255, (_CONST_SALT >> 16) & 255,
            (_CONST_SALT >> 8) & 255, _CONST_SALT & 255,
        ]

        xor_result = new_array[0]
        for i in range(1, len(new_array)):
            b = new_array[i]
            if isinstance(b, float):
                b = int(b)
            xor_result ^= b
        new_array.append(xor_result)

        # 奇偶拆位后交叉合并
        merge_array = new_array[0::2] + new_array[1::2]

        garbled = self._encoding_conversion2(
            2, 255,
            self._rc4_encrypt(
                b"\xff",
                self._encoding_conversion(*merge_array).encode("ISO-8859-1"),
            ).decode("ISO-8859-1"),
        )

        xb = ""
        for i in range(0, len(garbled), 3):
            xb += self._calc(ord(garbled[i]), ord(garbled[i + 1]), ord(garbled[i + 2]))

        return f"{url_path}&X-Bogus={xb}", xb, self.user_agent
