"""`schedule_task` 工具：模型可调用的「定个提醒」。

## 本模块只负责

把「用户说了一句带时间的话」变成一次工具调用，并把结果文本成给模型看的样子
（create / list / delete 三个动作）。它**不含**定时引擎——到点怎么唤醒、怎么重排、
怎么落盘，全在 `app/agent/proactivity/scheduler.py::TaskScheduler`。

## 为什么单独拆出来（P4）

`build_schedule_tool` 原本和 `TaskScheduler` 一起写在 `proactivity/scheduler.py`。
于是主循环为了拿这一个工具，不得不 `import proactivity.scheduler`，而调度器到点
又要 `import loop.chat` 跑一轮生成——两者互为依赖，和 `proactivity/stream.py`
构成 3 文件 / 3,355 行的强连通块（`STYLE.md §12.1`）。

把**工具**（给模型看的接口层）与**引擎**（真正的时间轮）分开后：

    loop/chat  ──►  tools/schedule          （叶子，只依赖 logger 与 ToolSpec）
    proactivity/scheduler ──► loop/chat     （单向，不再被回指）

环就断了。加新动作（如 `update`）改这里；改触发时机/重排逻辑改 `scheduler.py`。
"""

from __future__ import annotations

from app.core.logger import account_label

import re
from datetime import datetime

from app.agent import logger
from app.agent.tools.spec import ToolSpec
from app.domain.time_parser import parse_schedule

SCHEDULE_TASK_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["create", "list", "delete"],
            "description": "create 安排定时提醒；list 查看本会话已有任务；delete 按 job_id 删除任务。",
        },
        "trigger": {
            "type": "string",
            "description": "create 时的时间表达式（自然语言）。支持：明天早上8点 / 今晚10点 / 每天早上8点 / 每周五下午6点 / 每月1号上午9点 / 5分钟后 / 半小时后 / 每30分钟 / 08:30。",
        },
        "note": {
            "type": "string",
            "description": "create 时到点要做的内容 / 要说的话。",
        },
        "job_id": {
            "type": "string",
            "description": "delete 时的任务 id（来自 list 结果）。",
        },
    },
    "required": ["action"],
}


async def handle_schedule_tool(module, session_id: str, is_private: bool, args: dict) -> str:
    """schedule_task 工具处理器：create / list / delete。返回给 LLM 的结果文本。"""
    scheduler = getattr(module, "scheduler", None)
    if scheduler is None:
        return "error: 定时任务服务不可用"
    action = str(args.get("action") or "").strip().lower()
    bot_id = getattr(module, "bot_id", "?")

    if action == "create":
        trigger = str(args.get("trigger") or "").strip()
        note = str(args.get("note") or "").strip()
        if not trigger or not note:
            return "error: create 需要同时提供 trigger 与 note"
        existing = scheduler.find_similar(session_id, trigger)
        if existing is not None:
            logger.add_info(account_label(bot_id)).info(
                f"[定时任务] 工具 create 跳过重复 -> {session_id}: {trigger}"
            )
            return (
                f"success: 本会话已存在相同定时任务，id={existing.id}，重复方式={existing.repeat}，"
                f"下次触发时间={existing.next_at:%Y-%m-%d %H:%M:%S}，未重复创建。"
            )
        entry = await scheduler.schedule(session_id, {"trigger": trigger, "content": note})
        if entry is None:
            logger.add_info(account_label(bot_id)).warning(f"[定时任务] 工具 create 失败: trigger={trigger}")
            return (
                f"error: 无法解析时间表达式 {trigger!r}。支持格式：明天早上8点 / 今晚10点 / "
                "每天早上8点 / 每周五下午6点 / 每月1号上午9点 / 5分钟后 / 半小时后 / 每30分钟 / 08:30。"
                "请用这些格式重新尝试。"
            )
        logger.add_info(account_label(bot_id)).info(
            f"[定时任务] 工具 create -> {session_id}: {trigger} @ {entry.next_at:%Y-%m-%d %H:%M} ({entry.repeat})"
        )
        return (
            f"success: 已创建定时任务，id={entry.id}，重复方式={entry.repeat}，"
            f"下次触发时间={entry.next_at:%Y-%m-%d %H:%M:%S}。"
        )

    if action == "list":
        rows = [t for t in scheduler.status() if t["session_id"] == session_id]
        if not rows:
            return "当前会话没有定时任务"
        lines = []
        for t in rows:
            next_s = datetime.fromtimestamp(t["next_trigger_time"]).strftime("%Y-%m-%d %H:%M")
            lines.append(f"id={t['task_id']} | {t['repeat']} | 下次 {next_s} | {t['content'][:30]}")
        return "本会话定时任务:\n" + "\n".join(lines)

    if action == "delete":
        job_id = str(args.get("job_id") or "").strip()
        if not job_id:
            return "error: delete 需要提供 job_id"
        matches = [
            t for t in scheduler.status()
            if t["session_id"] == session_id
            and (t["task_id"] == job_id or t["task_id"].startswith(job_id))
        ]
        if not matches:
            return f"error: 未找到本会话的任务 {job_id}（用 list 查看）"
        if len(matches) > 1:
            ids = ", ".join(t["task_id"] for t in matches)
            return f"error: 任务 id {job_id} 前缀不唯一（{ids}），请使用完整 id 重试"
        task = matches[0]
        if scheduler.cancel(task["task_id"]):
            return f"success: 已删除定时任务 {task['task_id']}"
        return f"error: 删除任务 {task['task_id']} 失败"

    return "error: action 必须是 create / list / delete 之一"


def build_schedule_tool(module, session_id: str, is_private: bool) -> ToolSpec:
    """构造绑定到当前会话的 schedule_task 工具。"""

    async def _handler(ctx, args: dict) -> str:
        return await handle_schedule_tool(module, session_id, is_private, args)

    return ToolSpec(
        name="schedule_task",
        description=(
            "管理本会话的定时提醒/定时回复。当用户请求在特定时间做某事时（如"
            "\"明天早上8点提醒我\"、\"每周五发周报\"、\"5分钟后叫我\"），调用本工具安排；"
            "用户查询/取消已有提醒时也可调用（list/delete）。"
        ),
        parameters=SCHEDULE_TASK_SCHEMA,
        handler=_handler,
        source="system",
        category="定时任务",
    )


# ==================== 定时意图检测（确定性兜底） ====================

_SCHEDULE_VERBS = ("提醒", "定时", "记得", "叫我", "叫醒", "通知", "别忘了", "别忘", "到点")


_NOTE_STRIP_RES = (
    re.compile(r"大后天|后天|明天|今天|今晚|今日|每日|每天|每周|每月|每\s*\d+\s*[分钟小时天]"),
    re.compile(r"凌晨|清晨|早上|上午|中午|下午|傍晚|晚上|夜里|夜间|半夜"),
    re.compile(r"\d{1,2}\s*[:：]\s*\d{1,2}(?:\s*[:：]\s*\d{1,2})?"),
    re.compile(r"\d{1,2}\s*点\s*(?:\d{1,2}\s*分|\s*半)?"),
    re.compile(r"[一二两三四五六七八九十半]+\s*(?:个)?\s*[分钟小时天钟头]+\s*(?:之)?后"),
    re.compile(r"每\s*(?:隔)?\s*\d+\s*[分钟小时天]"),
)


def has_schedule_intent(text: str) -> bool:
    """判定用户消息是否含「定时提醒」意图：有提醒动词 + 时间可解析。

    用作兜底：LLM 未调用工具但用户确实提了定时请求时，由框架确定性排程。
    """
    text = (text or "").strip()
    if not text:
        return False
    if not any(v in text for v in _SCHEDULE_VERBS):
        return False
    return parse_schedule(text) is not None


def extract_reminder_note(text: str) -> str:
    """从请求里提取「到点要做的事」（去掉时间表达与客套）。"""
    cleaned = text or ""
    for pat in _NOTE_STRIP_RES:
        cleaned = pat.sub("", cleaned)
    for kw in ("提醒", "定时", "记得", "通知", "别忘了", "别忘", "叫醒"):
        idx = cleaned.find(kw)
        if idx >= 0:
            cleaned = cleaned[idx + len(kw):]
            break
    for pre in ("我", "你", "你帮", "帮我"):
        if cleaned.startswith(pre):
            cleaned = cleaned[len(pre):]
            break
    cleaned = cleaned.strip(" ，,。？?！!、的").strip()
    return cleaned or (text or "").strip()
