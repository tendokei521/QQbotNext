"""「为什么没回」的统一留痕。

背景：LLM 流水线里有多处静默 ``return``（群回复关闭、私聊关闭、触发未命中…），
用户表现为「机器人在群里一声不响，日志也干净」——这是最难排查的一类问题。
这里提供**唯一入口**，保证：

1. 格式统一：``[skip] reason=<原因> · <细节> · <去哪修>``；
2. 去重统一：同一 ``(账号, 原因, 细节)`` **进程内只记一次**——群没开、没填 key
   这类是「配置状态」而不是「瞬时事件」，每条消息都刷一遍警告只会把真问题淹掉。
   进程重启后去重状态清空，于是用户可见的那一条正好落在「启动后的第一次」
   （也就不会出现同一提示隔几分钟又刷一条的观感）；
   ``debug`` 级只进 debug.log、是纯技术证据，保留 60s 窗口，排查时可反复看到；
3. 可测试：日志出口是模块级 ``_emit``，测试可直接替换。

后续新增"跳过"分支一律走 ``log_skip``，不要各自 ``return``。
"""

from __future__ import annotations

import time

from app.core.logger import account_label, logger

#: debug 级（技术日志，只进 debug.log）的重复提醒间隔（秒）。
#: 用户可见级别（warning/info/error）不按时间窗口，而是「进程内只记一次」。
DEBUG_REPEAT_INTERVAL_S = 60.0

#: (账号, 原因, 细节) → 上次记录时刻（monotonic）；进程重启后自然清空。
_seen: dict[tuple[str, str, str], float] = {}

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
    """记录一条"本轮被跳过"的日志（去重）。

    Args:
        account_id: 账号 id（用于日志前缀与去重键）。
        reason: 机器可读的稳定原因码，如 ``group_disabled``。
        detail: 人类可读细节，如 ``群 123456``。**参与去重**：不同群/不同用户
            各自提醒一次，否则只会看到第一个。
        hint: 去哪修（一句话，界面文案口径见 docs/UI_COPY.md）。
        level: ``warning``（默认）/ ``info`` / ``error`` / ``debug``。
        interval_s: 覆盖默认去重窗口。``None`` = 按级别的默认（debug 60s，
            其余进程内只记一次）；``0`` = 不限流（测试/排查用）；
            ``>0`` = 该秒数内只记一次。

    Returns:
        True = 本次真的写了日志；False = 被去重。
    """
    account_key = str(account_id if account_id not in (None, "") else "?")
    key = (account_key, str(reason), str(detail))
    # 默认窗口：debug 是技术日志（可反复看），其余是用户可见提示（进程内一次）
    window: float | None = DEBUG_REPEAT_INTERVAL_S if str(level) == "debug" else None
    if interval_s is not None:
        window = max(0.0, float(interval_s))

    if window is None:
        # 进程内只记一次：同一「账号 + 原因 + 细节」重启后才会再提醒
        if key in _seen:
            return False
        _seen[key] = _now()
    elif window > 0:
        last = _seen.get(key)
        now = _now()
        if last is not None and now - last < window:
            return False
        _seen[key] = now

    parts = [f"[skip] reason={reason}"]
    if detail:
        parts.append(str(detail))
    if hint:
        parts.append(str(hint))
    _emit(account_key, " · ".join(parts), level)
    return True


def reset_skip_log_state() -> None:
    """清空去重状态（测试与「重连 / 配置变更后重新提醒」用）。"""
    _seen.clear()
