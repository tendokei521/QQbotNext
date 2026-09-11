"""WBI 签名实现（纯函数 + 常量，不依赖网络）。

自 2023-03 起 B 站对 ``x/player/wbi/playurl`` 等接口启用 WBI 签名校验，流程：

1. 从 ``x/web-interface/nav`` 取 ``wbi_img.img_url`` 与 ``sub_url``；
2. 各取文件名（去路径、去扩展名）拼成 64 字符原文 orig；
3. 用固定索引表 ``MIXIN_KEY_ENC_TAB`` 重排 orig，取前 32 位得到 mixin_key；
4. 参数按 key 升序 urlencode（值需先做 URL 安全过滤），末尾拼 mixin_key，取 MD5 得 ``w_rid``；
5. 请求时带上原始参数 + ``wts`` + ``w_rid``。

关键前提：``nav`` 接口**未登录**时返回 ``code=-101``，但 ``data.wbi_img`` 依然带有效密钥，
因此免登录也能完成签名（调用方须容忍该错误码）。

移植自 ``1/bili-video-parser/bili/wbi.py``（同作者工具链，逻辑保持逐行等价）。
"""

from __future__ import annotations

import hashlib
import time
import urllib.parse
from typing import Any, Dict, Mapping, Optional

# 官方前端固定使用的重排索引表，顺序不可改动。
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]

# 这些字符在 URL 中不安全，前端 encodeURIComponent 会转义，签名时需先剔除。
_FILTER_CHARS = "!'()*"


def _filename_of(url: str) -> str:
    """从 ``https://i0.hdslb.com/bfs/wbi/xxxx.png`` 提取 ``xxxx``。"""
    return url.rsplit("/", 1)[-1].split(".", 1)[0]


def get_mixin_key(orig: str) -> str:
    """按 MIXIN_KEY_ENC_TAB 重排原文并截取前 32 位。"""
    if len(orig) < 64:
        # 密钥原文恒为 64 字符，长度异常说明接口返回变了：直接报错，
        # 而不是静默产出错误签名（那会表现为难排查的 -403）。
        raise ValueError(f"WBI 密钥原文长度异常：期望 >=64，实际 {len(orig)}（orig={orig!r}）")
    return "".join(orig[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def extract_keys(img_url: str, sub_url: str) -> str:
    """由 img_url / sub_url 推导出 32 位 mixin_key。"""
    return get_mixin_key(_filename_of(img_url) + _filename_of(sub_url))


def _sanitize(value: Any) -> str:
    """把参数值转成与前端一致的字符串并过滤不安全字符。"""
    if isinstance(value, bool):
        # JS 侧布尔拼接为 "true"/"false"，Python 默认是 True/False。
        return "true" if value else "false"
    text = str(value)
    for ch in _FILTER_CHARS:
        text = text.replace(ch, "")
    return text


def enc_wbi(params: Mapping[str, Any], mixin_key: str, ts: Optional[int] = None) -> Dict[str, str]:
    """返回已签名的参数字典（含 ``wts`` 与 ``w_rid``）。"""
    signed = {k: _sanitize(v) for k, v in params.items() if v is not None}
    signed["wts"] = str(int(time.time()) if ts is None else int(ts))

    # 按 key 升序拼接成 query，再拼 mixin_key 求 MD5。
    query = urllib.parse.urlencode(sorted(signed.items()))
    signed["w_rid"] = hashlib.md5((query + mixin_key).encode("utf-8")).hexdigest()
    return signed


def signed_query(params: Mapping[str, Any], mixin_key: str) -> str:
    """签名并直接返回可拼接到 URL 的 query string。"""
    return urllib.parse.urlencode(sorted(enc_wbi(params, mixin_key).items()))
