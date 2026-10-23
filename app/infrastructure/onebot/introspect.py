"""账号对外视图（WebUI / 事件广播读的那份数据）。

## 为什么单独成文件

``get_bots_info`` 这类「把连接对象摊平成 JSON」的代码有五条硬要求，且每条都有过
真事故：不能回显 access_token、断线要用上次登录快照兜底、重连窗口里 bot_id 不能
变 null、连接方向要带出去、反向要标出「监听中」。它们与「连接/重连/掉线处理」的
状态机是两件事，混在一个文件里时改查询很容易顺手改坏状态机（反之亦然）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from app.infrastructure.config.ws_url import split_ws_url
from app.infrastructure.onebot.endpoint import FORWARD, normalize_mode


def effective_bot_id(conn: Any, last_account: dict | None) -> int | None:
    """对外公布的账号：实时 bot_id 优先，缺失时回退「上次登录账号」快照。

    WebUI 用这个值当 ``?bot_id=`` 去读写 agent 配置。若在重连窗口里直接吐
    ``conn.bot_id``（此时可能已被重置为 0），前端就会把账号判成 null / 用错
    账号，于是「配置看着一样却存到了另一个账号上、当前账号不生效」。
    """
    if conn.bot_id:
        return conn.bot_id
    try:
        fallback = int((last_account or {}).get("user_id") or 0)
    except (TypeError, ValueError):
        return None
    return fallback or None


def conn_mode(conn: Any) -> str:
    """连接方向（缺字段的老连接对象按正向处理）。"""
    return normalize_mode(getattr(conn, "conn_mode", FORWARD))


def bot_info(
    conn: Any,
    *,
    last_account: dict | None = None,
    listening: bool = False,
) -> dict:
    """单个账号的对外视图（不含 access_token）。"""
    base, _ = split_ws_url(conn.ws_url)
    return {
        "index": conn.index,
        "bot_id": effective_bot_id(conn, last_account),
        "owner_id": conn.owner_id,
        "status": conn.status,
        "ws_url": base,  # 对外只暴露基础地址（access_token 独立字段，不回显）
        "login_info": conn.login_info,
        # 上次登录账号快照 + 统一字段：连接中/已连接用实时，否则回退快照
        "last_account": last_account,
        "account": conn.login_info or last_account,
        "reconnect_attempts": conn.reconnect_attempts,
        "last_error": conn.last_error,
        "auto_connect": conn.auto_connect,
        "conn_mode": conn_mode(conn),
        # 反向模式：监听是否就绪（前端据此区分「监听中」与「已连接」）
        "listening": bool(listening),
        # 本次运行期间是否被用户手动断开（为 True 时监督循环不会自动重连）
        "suppress_auto_reconnect": bool(getattr(conn, "suppress_auto_reconnect", False)),
    }


def bots_info(
    connections: Iterable[Any],
    account_lookup: Callable[[], dict] | None = None,
    is_listening: Callable[[int | None], bool] | None = None,
) -> list[dict]:
    """全部账号的对外视图（按 index 升序）。"""
    accounts = account_lookup() if account_lookup else {}
    result: list[dict] = []
    for conn in sorted(connections, key=lambda c: c.index):
        result.append(bot_info(
            conn,
            last_account=accounts.get(conn.index),
            listening=is_listening(conn.index) if is_listening else False,
        ))
    return result
