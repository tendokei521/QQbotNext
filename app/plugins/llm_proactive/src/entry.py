"""单个定时任务的数据条目。

## 为什么单独成文件

`TaskEntry` 是纯数据（时间、重复方式、下次触发时间、内容），`TaskScheduler` 是驱动它的
683 行引擎。两者放一个文件时，读「一个任务长什么样」要先翻过几百行调度逻辑。
"""

from __future__ import annotations

import time
from datetime import datetime

# 定时触发（cron-wake）默认提示词：对齐 AstrBot PROACTIVE_AGENT_CRON_WOKE_SYSTEM_PROMPT 的轻量版


class TaskEntry:
    """单个定时任务。"""

    def __init__(
        self,
        *,
        task_id: str,
        session_id: str,
        is_group: bool,
        target: str,
        trigger_expr: str,
        content: str,
        repeat: str = "once",
        next_at: datetime,
        weekday: int | None = None,
        dom: int | None = None,
        interval_seconds: int | None = None,
        created_at: int | None = None,
        fired_count: int = 0,
        active: bool = True,
    ) -> None:
        self.id = task_id
        self.session_id = session_id
        self.is_group = is_group
        self.target = target
        self.trigger_expr = trigger_expr
        self.content = content
        self.repeat = repeat
        self.next_at = next_at
        self.weekday = weekday
        self.dom = dom
        self.interval_seconds = interval_seconds
        self.created_at = created_at or int(time.time())
        self.fired_count = fired_count
        self.active = active

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "is_group": self.is_group,
            "target": self.target,
            "trigger_expr": self.trigger_expr,
            "content": self.content,
            "repeat": self.repeat,
            "next_at": int(self.next_at.timestamp()),
            "weekday": self.weekday,
            "dom": self.dom,
            "interval_seconds": self.interval_seconds,
            "created_at": self.created_at,
            "fired_count": self.fired_count,
            "active": self.active,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TaskEntry:
        return cls(
            task_id=data["id"],
            session_id=data["session_id"],
            is_group=bool(data.get("is_group", False)),
            target=str(data.get("target", "")),
            trigger_expr=data.get("trigger_expr", ""),
            content=data.get("content", ""),
            repeat=data.get("repeat", "once"),
            next_at=datetime.fromtimestamp(float(data.get("next_at", 0))),
            weekday=data.get("weekday"),
            dom=data.get("dom"),
            interval_seconds=data.get("interval_seconds"),
            created_at=data.get("created_at"),
            fired_count=data.get("fired_count", 0),
            active=bool(data.get("active", True)),
        )
