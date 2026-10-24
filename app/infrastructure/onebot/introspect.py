"""账号对外视图（WebUI / 事件广播读的那份数据）。

## 为什么单独成文件

``get_bots_info`` 这类「把连接对象摊平成 JSON」的代码有五条硬要求，且每条都有过
真事故：不能回显 access_token、断线要用上次登录快照兜底、重连窗口里 bot_id 不能
变 null、连接方向要带出去、反向要标出「监听中」。它们与「连接/重连/掉线处理」的
状态机是两件事，混在一个文件里时改查询很容易顺手改坏状态机（反之亦然）。

## id 与 name 的分工

- ``id``：账号唯一凭据（用户添加时设定、不可变）。前端拿它发 ``?account_id=``，
  后端拿它当模块配置/会话/统计的 key。**不用于展示**。
- ``name``：展示名。卡片标题、顶栏选择器、日志都用它。
- ``bot_id``：当前登录的 QQ 号，纯运行时信息（登录后才有，断线用快照兜底）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from app.infrastructure.config.ws_url import split_ws_url
from app.infrastructure.onebot.endpoint import FORWARD, normalize_mode


def effective_bot_id(conn: Any, last_account: dict | None) -> int | None:
    """对外公布的 QQ 号：实时 bot_id 优先，缺失时回退「上次登录账号」快照。

    注意它只是**展示用的运行时信息**（卡片上「当前 QQ」、群列表归属），
    不再像历史实现那样兼任账号身份 —— 身份自 v4 起是 ``account_id``。
    若在重连窗口里直接吐 ``conn.bot_id``（此时可能已被重置为 0），前端会显示
    「未连接」一闪，所以这里仍保留快照兜底。
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
    account_id = getattr(conn, "account_id", None)
    return {
        "id": account_id,
        "name": getattr(conn, "account_name", "") or account_id or "",
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
    is_listening: Callable[[str | None], bool] | None = None,
) -> list[dict]:
    """全部账号的对外视图（保持传入顺序，即 ConfigService 的界面顺序）。"""
    accounts = account_lookup() if account_lookup else {}
    result: list[dict] = []
    for conn in connections:
        account_id = getattr(conn, "account_id", None)
        result.append(bot_info(
            conn,
            last_account=accounts.get(account_id),
            listening=is_listening(account_id) if is_listening else False,
        ))
    return result
