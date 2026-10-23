"""账号连接模式与 OneBot 端点解析。

## 两种连接方向

OneBot v11 的 WebSocket 有正反两个方向，本项目的账号配置用一个字段 ``conn_mode`` 区分：

- ``forward``（正向）：**我们连出去**。``ws_url`` 指向 OneBot 实现（NapCat / Lagrange …）
  暴露的 ws 地址，例如 ``ws://127.0.0.1:3001/?access_token=xxx``。
- ``reverse``（反向）：**我们监听、OneBot 实现连进来**。``ws_url`` 表示本地监听地址，
  例如 ``ws://0.0.0.0:3001/onebot/v11/ws?access_token=xxx``；接入方需要在
  NapCat 等实现里把「反向 WebSocket」指向同一个地址与路径。

两种模式下 ``ws_url`` 的形态一致（scheme + host + port + path + ``access_token``），
差别只在谁发起 TCP 连接，因此打码 / 拆分 / 合并逻辑（``ws_url.py``）两条路共用。

## 为什么单独成文件

模式归一与地址解析是「配置校验 + 监听器启动 + 前端表单提示」三处的共同前置步骤；
散在 gateway / config_service / api 里就会出现三份不一致的校验（历史教训：
一处认 ``ws://`` 一处认 ``ws://host/`` 时，前端存得下、后端连不上）。
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

#: 正向 WebSocket：框架作为客户端连出（历史默认，也是存量配置的语义）
FORWARD = "forward"
#: 反向 WebSocket：框架作为服务端监听，OneBot 实现连入
REVERSE = "reverse"

#: 对外展示用的模式标签（WebUI 下拉与文案共用一处真相）
MODE_LABELS: dict[str, str] = {
    FORWARD: "正向 WebSocket",
    REVERSE: "反向 WebSocket",
}

#: 向后兼容别名：配置里可能出现的旧写法 / 外部脚本写法
_MODE_ALIASES = {
    "": FORWARD,
    "ws": FORWARD,
    "forward": FORWARD,
    "forward_ws": FORWARD,
    "client": FORWARD,
    "reverse": REVERSE,
    "ws_reverse": REVERSE,
    "reverse_ws": REVERSE,
    "server": REVERSE,
}


def normalize_mode(value: object) -> str:
    """把任意来源的连接模式归一为 ``forward`` / ``reverse``。

    无法识别（含 ``None`` / 空串 / 历史配置缺字段）一律当正向：存量账号都是正向，
    升级后必须继续按原样工作，不能因为多了个字段就默默改行为。
    """
    if isinstance(value, str):
        key = value.strip().lower()
        if key in _MODE_ALIASES:
            return _MODE_ALIASES[key]
    return FORWARD


def is_reverse(value: object) -> bool:
    return normalize_mode(value) == REVERSE


@dataclass(frozen=True)
class Endpoint:
    """解析后的 ws 地址（正反向共用）。

    ``token`` 为 ``access_token`` 查询参数（OneBot 约定的鉴权方式），其余 query
    参数保留在 ``query`` 里，供需要透传的调用方使用。
    """

    url: str
    scheme: str
    host: str
    port: int | None
    path: str
    token: str
    query: str = ""

    @property
    def is_tls(self) -> bool:
        return self.scheme in ("wss", "https")

    @property
    def default_port(self) -> int:
        return 443 if self.is_tls else 80

    def bindable(self) -> tuple[str, int]:
        """反向监听用的 (host, port)：缺省端口按 scheme 补齐。"""
        return self.host, self.port or self.default_port


class EndpointError(ValueError):
    """地址不可用（缺少 host / 端口非法 / scheme 不支持）。"""


def parse_endpoint(url: str, *, require_port: bool = False) -> Endpoint:
    """解析 ws 地址；不合法时抛 :class:`EndpointError`（消息可直接展示给用户）。

    ``require_port=True`` 用于反向监听：监听地址没有明确端口就等于「随便挑一个」，
    用户无从在 OneBot 实现里填写目标，故直接判为配置错误。
    """
    raw = (url or "").strip()
    if not raw:
        raise EndpointError("地址为空")
    try:
        parts = urlsplit(raw)
    except ValueError as e:
        raise EndpointError(f"地址无法解析: {e}") from e
    scheme = (parts.scheme or "").lower()
    if scheme not in ("ws", "wss", "http", "https"):
        raise EndpointError(f"不支持的协议: {scheme or '(空)'}（应为 ws:// 或 wss://）")
    host = parts.hostname or ""
    if not host:
        raise EndpointError("缺少主机名（形如 ws://127.0.0.1:3001/）")
    try:
        port = parts.port
    except ValueError as e:
        raise EndpointError(f"端口不合法: {e}") from e
    if require_port and port is None:
        raise EndpointError("反向监听地址必须写明端口（形如 ws://0.0.0.0:3001/onebot/v11/ws）")

    token = ""
    kept: list[str] = []
    for param in (parts.query or "").split("&"):
        if param.startswith("access_token="):
            token = param[len("access_token="):]
        elif param:
            kept.append(param)
    return Endpoint(
        url=raw,
        scheme=scheme,
        host=host,
        port=port,
        path=parts.path or "/",
        token=token,
        query="&".join(kept),
    )
