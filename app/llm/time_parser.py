"""中文时间表达式解析 → 定时任务首次触发时间与重复规则。

与系统提示词「定时任务协议」中约束的格式保持一致：

- 绝对时间：HH:MM[:SS]、"X点[X分|半]"（可选 凌晨/早上/上午/中午/下午/傍晚/晚上/夜里 时段）
- 日期修饰：今天/今晚/明天/后天/大后天、"周X/星期X/礼拜X"、"下周X"
- 重复：每天/每日/天天、"每周X"、"每月X日"、"每X分钟/每X小时/每X天"
- 相对：X秒后/X分钟后/X小时后/X天后、"半小时后"

返回 {"next_at": datetime, "repeat": str, ...}；无法解析返回 None。
repeat 取值：once / daily / weekly / monthly / interval。
"""

from __future__ import annotations

import calendar
import re
from datetime import datetime, timedelta
from typing import Dict, Optional

# 时段 → 未跟数字时的默认小时
_PERIOD_DEFAULT_HOUR = {
    "凌晨": 0, "清晨": 6, "早上": 8, "上午": 10, "中午": 12,
    "下午": 15, "傍晚": 17, "晚上": 20, "夜里": 22, "夜间": 22, "半夜": 23,
}
_PERIODS = ("凌晨", "清晨", "早上", "上午", "中午", "下午", "傍晚", "晚上", "夜里", "夜间", "半夜")

# 中文星期 → 0(周一)~6(周日)
_CN_WEEKDAY = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}

# 中文数字（用于 "十分钟后 / 半小时" 等）
_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
           "七": 7, "八": 8, "九": 9, "十": 10, "半": 0.5}


def _cn_to_int(s: str) -> Optional[float]:
    """简单中文数字 → 数值（十 及 十N 组合、单个数字、半）。"""
    if not s:
        return None
    if s in _CN_NUM:
        return _CN_NUM[s]
    if "十" in s:
        parts = s.split("十")
        tens = _CN_NUM.get(parts[0], 1) if parts[0] else 1
        ones = _CN_NUM.get(parts[1], 0) if len(parts) > 1 and parts[1] else 0
        return tens * 10 + ones
    return None


def _find_period(text: str) -> Optional[str]:
    if "今晚" in text:
        return "晚上"
    for p in _PERIODS:
        if p in text:
            return p
    return None


def _adjust_12h(h: int, period: Optional[str]) -> int:
    """把 12 小时制数字按时段换算为 24 小时制。"""
    if period in ("凌晨", "半夜"):
        return 0 if h == 12 else h % 12
    if period in ("早上", "上午", "清晨"):
        return 12 if h == 12 else h
    if period == "中午":
        return 12
    if period in ("下午", "傍晚"):
        return h + 12 if h < 12 else h
    if period in ("晚上", "夜里", "夜间"):
        return 0 if h == 12 else (h + 12 if h < 12 else h)
    return h


def _parse_clock(text: str) -> Optional[tuple]:
    """解析时刻 → (hour, minute, second)；无法解析返回 None。"""
    period = _find_period(text)

    m = re.search(r'(\d{1,2})\s*[:：]\s*(\d{1,2})(?:\s*[:：]\s*(\d{1,2}))?', text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        s = int(m.group(3) or 0)
        if period:
            h = _adjust_12h(h, period)
        if h > 23 or mi > 59 or s > 59:
            return None
        return h, mi, s

    m = re.search(r'(\d{1,2})\s*点\s*(?:(\d{1,2})\s*分|\s*半)?', text)
    if m:
        h = int(m.group(1))
        if period:
            h = _adjust_12h(h, period)
        elif h > 23:
            return None
        mi = int(m.group(2)) if m.group(2) else (30 if "半" in text else 0)
        if mi > 59:
            return None
        return h, mi, 0

    if period:
        return _PERIOD_DEFAULT_HOUR[period], 0, 0
    return None


def _resolve_weekday(w: str) -> Optional[int]:
    if w in _CN_WEEKDAY:
        return _CN_WEEKDAY[w]
    if w.isdigit():
        return (int(w) - 1) % 7
    return None


def _parse_day(text: str) -> dict:
    """解析日期/重复修饰 → 组合结果。"""
    result = {"day_offset": None, "repeat": None, "weekday": None, "dom": None, "next_week": False}

    # 每月X日
    m = re.search(r'每月\s*(\d{1,2})\s*(?:日|号)', text)
    if m:
        result["repeat"] = "monthly"
        result["dom"] = int(m.group(1))
        return result

    # 每周X / 每星期X / 每礼拜X
    m = re.search(r'每(?:周|星期|礼拜)([一二三四五六日天1-7])', text)
    if m:
        result["repeat"] = "weekly"
        result["weekday"] = _resolve_weekday(m.group(1))
        return result

    # 每天/每日/天天
    if any(k in text for k in ("每天", "每日", "天天")):
        result["repeat"] = "daily"
        return result

    # 下周X（严格下周）
    m = re.search(r'(?:下|下个|下周)(?:周|星期|礼拜)([一二三四五六日天1-7])', text)
    if m:
        result["weekday"] = _resolve_weekday(m.group(1))
        result["next_week"] = True
        return result

    # 裸 周X / 星期X / 礼拜X → 下一次该星期几
    m = re.search(r'(?:周|星期|礼拜)([一二三四五六日天1-7])', text)
    if m:
        result["weekday"] = _resolve_weekday(m.group(1))
        return result

    # 大后天/后天/明天/今天
    if "大后天" in text:
        result["day_offset"] = 3
    elif "后天" in text:
        result["day_offset"] = 2
    elif "明天" in text or "明日" in text:
        result["day_offset"] = 1
    elif any(k in text for k in ("今天", "今晚", "今日")):
        result["day_offset"] = 0
    return result


def _monthly_target(now: datetime, dom: int, h: int, mi: int, s: int) -> datetime:
    """本月 dom 日时刻；若已过则推至下月。dom 超过当月天数时钳制到月末。"""

    def _make(y: int, mo: int) -> datetime:
        last = calendar.monthrange(y, mo)[1]
        return datetime(y, mo, min(dom, last), h, mi, s)

    target = _make(now.year, now.month)
    if target <= now:
        y, mo = (now.year, now.month + 1) if now.month < 12 else (now.year + 1, 1)
        target = _make(y, mo)
    return target


def _compute_first(day: dict, h: int, mi: int, s: int, now: datetime) -> tuple:
    """按日期修饰与时刻计算首次触发时间 + 重复规则。"""
    base = now.replace(hour=h, minute=mi, second=s, microsecond=0)

    if day["repeat"] == "daily":
        return (base + timedelta(days=1)) if base <= now else base, "daily"

    if day["repeat"] == "weekly":
        days = (day["weekday"] - now.weekday()) % 7
        target = (now + timedelta(days=days)).replace(hour=h, minute=mi, second=s, microsecond=0)
        if target <= now:
            target += timedelta(days=7)
        return target, "weekly"

    if day["repeat"] == "monthly":
        return _monthly_target(now, day["dom"], h, mi, s), "monthly"

    if day["weekday"] is not None:
        if day["next_week"]:
            days = ((day["weekday"] - now.weekday()) % 7) + 7
        else:
            days = (day["weekday"] - now.weekday()) % 7
        target = (now + timedelta(days=days)).replace(hour=h, minute=mi, second=s, microsecond=0)
        if target <= now:
            target += timedelta(days=7)
        return target, "once"

    if day["day_offset"] is not None:
        target = (now + timedelta(days=day["day_offset"])).replace(
            hour=h, minute=mi, second=s, microsecond=0
        )
        if target <= now:
            target += timedelta(days=1)
        return target, "once"

    # 裸时刻 → 下一次该时刻
    return (base + timedelta(days=1)) if base <= now else base, "once"


def _unit_seconds(n: float, unit: str) -> Optional[int]:
    if unit == "秒":
        return int(n)
    if unit in ("分钟", "分"):
        return int(n * 60)
    if unit in ("小时", "钟头"):
        return int(n * 3600)
    if unit == "天":
        return int(n * 86400)
    return None


def parse_schedule(text: str, now: Optional[datetime] = None) -> Optional[dict]:
    """解析定时任务时间表达式。

    Returns:
        {"next_at": datetime, "repeat": str, "weekday": int|None,
         "dom": int|None, "interval_seconds": int|None}；无法解析返回 None。
    """
    now = now or datetime.now()
    text = (text or "").strip()
    if not text:
        return None

    # 1) 每X分钟/每X小时/每X天 → 间隔重复
    m = re.search(r'每\s*(?:隔)?\s*(\d+|[一二两三四五六七八九十]+)\s*(分钟|分|小时|钟头|天)', text)
    if m:
        raw = m.group(1)
        n = float(raw) if raw.isdigit() else (_cn_to_int(raw) or 0)
        if n <= 0:
            return None
        secs = _unit_seconds(n, m.group(2))
        if secs is None:
            return None
        return {
            "next_at": now + timedelta(seconds=secs),
            "repeat": "interval",
            "weekday": None,
            "dom": None,
            "interval_seconds": secs,
        }

    # 2) 相对时间：X秒/分钟/小时/天后、半小时后
    m = re.search(
        r'(\d+(?:\.\d+)?|[一二两三四五六七八九十半]+)\s*(?:个)?\s*(秒|分钟|分|小时|钟头|天)\s*(?:之)?后',
        text,
    )
    if m:
        raw = m.group(1)
        if raw.replace(".", "", 1).isdigit():
            n = float(raw)
        else:
            n = _cn_to_int(raw) or 0
        secs = _unit_seconds(n, m.group(2))
        if secs is None or secs <= 0:
            return None
        return {
            "next_at": now + timedelta(seconds=secs),
            "repeat": "once",
            "weekday": None,
            "dom": None,
            "interval_seconds": None,
        }

    # 3) 日期修饰 + 时刻
    day = _parse_day(text)
    clock = _parse_clock(text)
    if clock is None:
        return None
    target, repeat = _compute_first(day, clock[0], clock[1], clock[2], now)
    return {
        "next_at": target,
        "repeat": repeat,
        "weekday": day["weekday"],
        "dom": day["dom"],
        "interval_seconds": None,
    }


def advance_repeat(
    current: datetime,
    *,
    repeat: str,
    weekday: Optional[int] = None,
    dom: Optional[int] = None,
    interval_seconds: Optional[int] = None,
) -> datetime:
    """周期性任务的下一触发时间。"""
    if repeat == "daily":
        return current + timedelta(days=1)
    if repeat == "weekly":
        if weekday is not None:
            days = (weekday - current.weekday()) % 7
            return current + timedelta(days=days or 7)
        return current + timedelta(days=7)
    if repeat == "monthly":
        return _monthly_target(current, dom or 1, current.hour, current.minute, current.second)
    if repeat == "interval":
        return current + timedelta(seconds=interval_seconds or 0)
    return current + timedelta(days=1)
