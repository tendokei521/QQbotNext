"""抖音 ``a_bogus`` 签名（纯 Python，无第三方依赖）。

抖音 Web API 自 2024 年起要求 ``a_bogus`` 查询参数：由「随机串 + 参数指纹」经
SM3 → RC4 → 自定义 base64 编码得到。本实现按算法结构重写，常量（字符表、UA 特征码、
浏览器指纹模板）与公开逆向实现一致：

- 上游参考：``DLWangSan/douyin_parse`` 的 ``abogus.py``（基于 Evil0ctal/Douyin_TikTok_Download_API，
  原始实现来自 JoeanAmier/TikTokDownloader，**GPL v3**）——见本插件 README「算法来源与许可」；
- SM3 由本插件自带的 ``sm3.py`` 提供，不依赖 ``gmssl``。

签名的正确性靠 ``tests/test_douyin_abogus.py`` 的**定向量**保证：同一组
（参数、时间戳、随机数）必须得到同一串。
"""

from __future__ import annotations

from random import choice, randint, random
from time import time
from urllib.parse import quote, urlencode

from .sm3 import sm3_hex

__all__ = ["ABogus"]

# 自定义 base64 字符表（末位 '=' 仅占位，编码结果只取前 64 个字符）
_CHARSET_S4 = "Dkdpgh2ZmsQB80/MfvV36XI1R45-WUAlEixNLwoqYTOPuzKFjJnry79HbGcaStCe="


class ABogus:
    """生成抖音 API 请求所需的 ``a_bogus`` 签名参数。"""

    _end_string = "cus"
    #: 默认浏览器指纹模板（MacIntel，与上游一致）
    _browser = "1536|742|1536|864|0|0|0|0|1536|864|1536|864|1536|742|24|24|MacIntel"
    _reg_init = [
        1937774191, 1226093241, 388252375, 3666478592,
        2842636476, 372324522, 3817729613, 2969243214,
    ]

    def __init__(self, platform: str | None = None) -> None:
        #: UA 特征码：与请求头里的 UA 版本对应，换 UA 需同步换这串
        self.ua_code = [
            76, 98, 15, 131, 97, 245, 224, 133, 122, 199,
            241, 166, 79, 34, 90, 191, 128, 126, 122, 98,
            66, 11, 14, 40, 49, 110, 110, 173, 67, 96, 138, 252,
        ]
        self.browser = self._generate_browser_info(platform) if platform else self._browser
        self.browser_len = len(self.browser)
        self.browser_code = self._char_code_at(self.browser)

    # ---------- 随机串生成 ----------

    @classmethod
    def _list_1(cls, random_num=None, a: int = 170, b: int = 85, c: int = 45) -> list:
        return cls._random_list(random_num, a, b, 1, 2, 5, c & a)

    @classmethod
    def _list_2(cls, random_num=None, a: int = 170, b: int = 85) -> list:
        return cls._random_list(random_num, a, b, 1, 0, 0, 0)

    @classmethod
    def _list_3(cls, random_num=None, a: int = 170, b: int = 85) -> list:
        return cls._random_list(random_num, a, b, 1, 0, 5, 0)

    @staticmethod
    def _random_list(a=None, b: int = 170, c: int = 85, d: int = 0, e: int = 0, f: int = 0, g: int = 0) -> list:
        r = a if a is not None else (random() * 10000)
        v = [r, int(r) & 255, int(r) >> 8]
        return [
            v[1] & b | d,
            v[1] & c | e,
            v[2] & b | f,
            v[2] & c | g,
        ]

    @staticmethod
    def _from_char_code(*args) -> str:
        return "".join(chr(code) for code in args)

    def _generate_string_1(self, r1=None, r2=None, r3=None) -> str:
        return (
            self._from_char_code(*self._list_1(r1))
            + self._from_char_code(*self._list_2(r2))
            + self._from_char_code(*self._list_3(r3))
        )

    # ---------- 参数指纹串生成 ----------

    def _generate_string_2(self, url_params: str, method: str = "GET",
                           start_time: int = 0, end_time: int = 0) -> str:
        a = self._list_4_list(url_params, method, start_time, end_time)
        # 校验位只覆盖参数指纹段，**必须在拼接浏览器指纹之前**算（否则签名尾部错）
        check = self._end_check_num(a)
        a.extend(self.browser_code)
        a.append(check)
        return self._rc4_encrypt(self._from_char_code(*a), "y")

    def _list_4_list(self, url_params: str, method: str = "GET",
                     start_time: int = 0, end_time: int = 0) -> list:
        start_time = start_time or int(time() * 1000)
        end_time = end_time or (start_time + randint(4, 8))
        params_arr = self._generate_params_code(url_params)
        method_arr = self._generate_method_code(method)
        return self._list_4(
            (end_time >> 24) & 255, params_arr[21], self.ua_code[23],
            (end_time >> 16) & 255, params_arr[22], self.ua_code[24],
            (end_time >> 8) & 255, (end_time >> 0) & 255,
            (start_time >> 24) & 255, (start_time >> 16) & 255,
            (start_time >> 8) & 255, (start_time >> 0) & 255,
            method_arr[21], method_arr[22],
            (end_time // 256 // 256 // 256 // 256) & 0xFF,
            (start_time // 256 // 256 // 256 // 256) & 0xFF,
            self.browser_len,
        )

    @staticmethod
    def _list_4(a, b, c, d, e, f, g, h, i, j, k, m, n, o, p, q, r) -> list:
        return [
            44, a, 0, 0, 0, 0, 24, b, n, 0, c, d, 0, 0, 0, 1, 0, 239,
            e, o, f, g, 0, 0, 0, 0, h, 0, 0, 14, i, j, 0, k, m, 3, p, 1,
            q, 1, r, 0, 0, 0,
        ]

    # ---------- SM3 ----------

    @classmethod
    def _generate_method_code(cls, method: str) -> list:
        return cls._sm3_to_array(cls._sm3_to_array(method + cls._end_string))

    def _generate_params_code(self, params: str) -> list:
        return self._sm3_to_array(self._sm3_to_array(params + self._end_string))

    @staticmethod
    def _sm3_to_array(data) -> list:
        raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        hex_digest = sm3_hex(raw)
        return [int(hex_digest[i:i + 2], 16) for i in range(0, len(hex_digest), 2)]

    # ---------- 工具 ----------

    @staticmethod
    def _end_check_num(arr: list) -> int:
        """尾部异或校验位。"""
        r = 0
        for i in arr:
            r ^= i
        return r

    @staticmethod
    def _char_code_at(s: str) -> list:
        return [ord(c) for c in s]

    @staticmethod
    def _rc4_encrypt(plaintext: str, key: str) -> str:
        s = list(range(256))
        j = 0
        for i in range(256):
            j = (j + s[i] + ord(key[i % len(key)])) % 256
            s[i], s[j] = s[j], s[i]
        i = j = 0
        result = []
        for ch in plaintext:
            i = (i + 1) % 256
            j = (j + s[i]) % 256
            s[i], s[j] = s[j], s[i]
            result.append(chr(s[(s[i] + s[j]) % 256] ^ ord(ch)))
        return "".join(result)

    @staticmethod
    def _generate_browser_info(platform: str) -> str:
        """随机生成一份浏览器指纹（不传 platform 时用固定模板）。"""
        inner_w = randint(1280, 1920)
        inner_h = randint(720, 1080)
        outer_w = randint(inner_w, 1920)
        outer_h = randint(inner_h, 1080)
        values = [
            inner_w, inner_h, outer_w, outer_h,
            0, choice((0, 30)), 0, 0,
            outer_w, outer_h, outer_w, outer_h,
            inner_w, inner_h, 24, 24,
            platform,
        ]
        return "|".join(str(v) for v in values)

    # ---------- 结果编码 ----------

    @staticmethod
    def _generate_result(s: str) -> str:
        """自定义 base64 编码（与上游一致：末段补 ``=``）。"""
        result = []
        for i in range(0, len(s), 3):
            remaining = len(s) - i
            if remaining >= 3:
                n = (ord(s[i]) << 16) | (ord(s[i + 1]) << 8) | ord(s[i + 2])
                result.append(_CHARSET_S4[(n >> 18) & 63])
                result.append(_CHARSET_S4[(n >> 12) & 63])
                result.append(_CHARSET_S4[(n >> 6) & 63])
                result.append(_CHARSET_S4[n & 63])
            elif remaining == 2:
                n = (ord(s[i]) << 16) | (ord(s[i + 1]) << 8)
                result.append(_CHARSET_S4[(n >> 18) & 63])
                result.append(_CHARSET_S4[(n >> 12) & 63])
                result.append(_CHARSET_S4[(n >> 6) & 63])
            else:
                n = ord(s[i]) << 16
                result.append(_CHARSET_S4[(n >> 18) & 63])
                result.append(_CHARSET_S4[(n >> 12) & 63])
        result.append("=" * ((4 - len(result) % 4) % 4))
        return "".join(result)

    # ---------- 主接口 ----------

    def get_value(self, url_params, method: str = "GET",
                  start_time: int = 0, end_time: int = 0,
                  random_num_1=None, random_num_2=None, random_num_3=None) -> str:
        """生成 ``a_bogus`` 签名值（调用方负责 URL 编码后拼进查询串）。

        Args:
            url_params: 参与签名的查询参数（dict 或已编码的 str）
            method: HTTP 方法
            start_time / end_time: 毫秒时间戳；传 0 表示取当前时间
            random_num_1/2/3: 随机串种子；传 None 表示真随机（生产路径）
        """
        if isinstance(url_params, dict):
            url_params = urlencode(url_params)
        string_1 = self._generate_string_1(random_num_1, random_num_2, random_num_3)
        string_2 = self._generate_string_2(url_params, method, start_time, end_time)
        return self._generate_result(string_1 + string_2)

    @staticmethod
    def build_signed_url(base_url: str, params: dict, bogus: str) -> str:
        """把参数与签名拼成完整 URL（``a_bogus`` 只编码一次，避免二次编码）。"""
        return f"{base_url}?{urlencode(params)}&a_bogus={quote(bogus, safe='')}"
