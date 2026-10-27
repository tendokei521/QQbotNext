"""「为什么没回」的统一留痕。

背景：LLM 流水线里有多处静默 ``return``（群回复关闭、私聊关闭、触发未命中…），
用户表现为「机器人在群里一声不响，日志也干净」——这是最难排查的一类问题。
这里提供**唯一入口**，保证：

1. 格式统一：``[skip] reason=<原因> · <细节> · <去哪修>``；
2. 限流统一：同一 ``(账号, 原因)`` 默认 60s 内只记一次，避免刷屏；
3. 可测试：日志出口是模块级 ``_emit``，测试可直接替换。

后续新增"跳过"分支一律走 ``log_skip``，不要各自 ``return``。
"""

from __future__ import annotations

import time

from app.core.logger import account_label, logger

#: 同一 (账号, 原因) 的最小记录间隔（秒）
SKIP_LOG_INTERVAL_S = 60.0

#: (账号, 原因) → 上次记录时刻（monotonic）
_last_logged: dict[tuple[str, str], float] = {}

#: 时钟可替换（测试注入）
_now = time.monotonic


def _emit(account_id: str, message: str, level: str) -> None:
    """真正的日志出口；测试里替换它即可断言。"""
    log = logger.add_info(account_label(account_id))
    getattr(log, level, log.warning)(message)


def log_skip(
    account_id: object,
    reason: str,
    detail: str = "",
    *,
    hint: str = "",
    level: str = "warning",
    interval_s: float | None = None,
) -> bool:
    """记录一条"本轮被跳过"的日志（限流）。

    Args:
        account_id: 账号 id（用于日志前缀与限流键）。
        reason: 机器可读的稳定原因码，如 ``group_disabled``。
        detail: 人类可读细节，如 ``group=123456``。
        hint: 去哪修（一句话，界面文案口径见 docs/UI_COPY.md）。
        level: ``warning``（默认）/ ``info`` / ``debug``。
        interval_s: 覆盖默认限流间隔；``0`` 表示不限制（测试用）。

    Returns:
        True = 本次真的写了日志；False = 被限流。
    """
    key = (str(account_id if account_id not in (None, "") else "?"), str(reason))
    window = SKIP_LOG_INTERVAL_S if interval_s is None else max(0.0, float(interval_s))
    if window > 0:
        last = _last_logged.get(key)
        now = _now()
        if last is not None and now - last < window:
            return False
        _last_logged[key] = now

    parts = [f"[skip] reason={reason}"]
    if detail:
        parts.append(str(detail))
    if hint:
        parts.append(str(hint))
    _emit(key[0], " · ".join(parts), level)
    return True


def reset_skip_log_state() -> None:
    """清空限流状态（测试与「重连后重新提醒」用）。"""
    _last_logged.clear()
