"""WebSocket URL 的打码与拆合。

## 为什么单独成文件

`ConfigService` 有 735 行（SQLite + 内存缓存 + 变更广播）；这四个函数只处理
「`ws://host:port/path` 怎么在对外展示时打码、编辑时怎么拆开再拼回」，与配置读写无关。
对外展示打码是安全边界，单独放一处才好审。
"""

from __future__ import annotations

from urllib.parse import quote, unquote

#: 对外展示时替换 access_token 的哨兵值（写回时 `restore_ws_url` 会认出它并保留旧值）
ACCESS_TOKEN_MASK = "****"


def mask_ws_url(url: str) -> str:
    """把 ws_url 中的 access_token 打码，避免 WebUI / 页面 HTML 泄露令牌。"""
    if not url or "access_token=" not in url:
        return url
    import re

    return re.sub(r"(access_token=)[^&]*", r"\g<1>" + ACCESS_TOKEN_MASK, url)


def split_ws_url(url: str) -> tuple[str, str]:
    """把完整 ws_url 拆成 (基础地址, access_token)。

    仅拆出 access_token 参数，query 中其余参数保留在基础地址里。
    token 按 URL 解码：配置里存的是编码后的值（中文/`&` 会变成 %XX），
    拆分后给表单/监听器用的必须是真实值。
    """
    if not url or "?" not in url:
        return (url or ""), ""
    base, query = url.split("?", 1)
    token = ""
    kept = []
    for param in query.split("&"):
        if not param:
            continue
        key, _, value = param.partition("=")
        if unquote(key) == "access_token":
            token = unquote(value)
        else:
            kept.append(param)
    if kept:
        return f"{base}?{'&'.join(kept)}", token
    return base, token


def join_ws_url(base: str, token: str) -> str:
    """把基础地址与 access_token 拼回完整 ws_url（无 token 时原样返回）。

    token 必须 URL 编码：非 ASCII 或含 ``&``/``=`` 的令牌不编码会被拆成多个
    query 参数（或被 HTTP 客户端改写），结果永远连不上（见 split_ws_url 的解码）。
    """
    base = (base or "").strip()
    if not token:
        return base
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}access_token={quote(str(token), safe='')}"


def restore_ws_url(masked: str, original: str) -> str:
    """保存时：提交的是打码 URL → 用原 URL 的真实 access_token 回填。"""
    if not masked or ACCESS_TOKEN_MASK not in masked or not original:
        return masked
    import re

    match = re.search(r"access_token=([^&]*)", original)
    if not match:
        return masked
    return re.sub(r"access_token=" + re.escape(ACCESS_TOKEN_MASK), f"access_token={match.group(1)}", masked)
