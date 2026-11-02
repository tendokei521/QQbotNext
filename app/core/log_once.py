"""「配置不全」类告警的进程内去重：同一件事只提醒一次。

## 为什么

没选模型、引用的模型被删、不在允许清单里……这些是**配置状态**，不是瞬时事件。
只要配置没修好，每条消息都会再走一遍判断；每轮刷一条 WARNING 只会把真问题淹掉
（用户看到的是"日志一直在报警"，反而不知道哪一条才要处理）。

这与 `app/core/skip_log.py` 的分工：

- ``skip_log``：「这轮为什么没回」的统一留痕，带 ``reason=`` / 修复提示的结构；
- ``log_once``：普通告警的**去重出口**，不改文案、不改级别，只保证同一件事一次。

去重键是 ``(账号, 事件 key)``：不同账号各自提醒一次；进程重启后清空（下次启动会再
提醒一次，正好对应"刚启动就该看看配置"）。配置修好之后如果又坏了，需要重启才会再次
提醒——这是有意的取舍：宁可少提醒，也不要把日志刷成噪音。
"""

from __future__ import annotations

from app.core.logger import account_label, logger

#: (账号, 事件 key) → 已提醒过；进程重启后自然清空
_seen: set[tuple[str, str]] = set()


def reset_log_once_state() -> None:
    """清空去重状态（测试用；也方便"配置变更后希望重新提醒"的调用方）。"""
    _seen.clear()


def warn_once(
    account_id: object,
    key: str,
    message: str,
    *,
    level: str = "warning",
) -> bool:
    """同一个 ``(账号, key)`` 进程内只写一次日志。

    Args:
        account_id: 账号 id（用于日志前缀与去重键；空值归为 ``?``）。
        key: 稳定的短事件码，如 ``provider.no_model``；**不要**把模型名/异常文本
            拼进 key，否则"同一件事"会因为细节不同而绕过去重。
        message: 要打印的内容（与普通告警一致）。
        level: ``warning``（默认）/ ``info`` / ``error``。

    Returns:
        True = 本次真的写了日志；False = 之前已经提醒过、被去重。
    """
    account_key = str(account_id if account_id not in (None, "") else "?")
    dedupe_key = (account_key, str(key))
    if dedupe_key in _seen:
        return False
    _seen.add(dedupe_key)
    log = logger.add_info(account_label(account_key))
    getattr(log, level, log.warning)(str(message))
    return True
