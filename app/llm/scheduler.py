"""定时任务服务（框架级 CronManager）：识别对话中的定时需求，到点以一次 LLM 请求自然发言。

机制（化用 AstrBot CronJobManager / PROACTIVE_AGENT_CRON_WOKE_SYSTEM_PROMPT）：
- 每个 Bot 实例一个 TaskScheduler，随框架生命周期创建/销毁；
- 任务持久化到 llm 数据目录 tasks_data_{bot_id}.json，重启后自动恢复未到期的任务；
- 触发：task_manager.create_task 睡眠到 next_at → 像主动消息一样做一次
  带系统提示词的 LLM 请求（结合会话历史）→ 自然发言 → 周期任务推进到下一触发时间；
- LLM 生成失败时兜底发送任务自带的固定内容；
- schedule_task 原生工具（create/list/delete）+ 定时意图确定性兜底；
- 周期支持：once / daily / weekly / monthly / interval（由 time_parser 解析）。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import datetime

from app.llm import logger, llm_data_dir, safe_bot_id
from app.llm.group_context import (
    build_group_env_text,
    fetch_group_name,
    fetch_group_online_history,
    format_history_for_llm,
)
from app.llm.prompt import build_messages
from app.llm.providers import chat_with_fallback, get_provider
from app.llm.session import SessionManager
from app.llm.tags import strip_all_tags
from app.llm.time_parser import advance_repeat, parse_schedule
from app.llm.tool import ToolSpec

# 定时触发（cron-wake）默认提示词：对齐 AstrBot PROACTIVE_AGENT_CRON_WOKE_SYSTEM_PROMPT 的轻量版
DEFAULT_SCHEDULE_PROMPT = (
    "你被一个定时任务唤醒，这不是一次用户对话。\n"
    "规则：\n"
    "1. 这不是聊天轮次：不要打招呼，不要反问用户。\n"
    "2. 结合最近的历史对话理解与用户的关系和上下文，用符合你人设的语气自然开口。\n"
    "3. 自然地说明你联系的原因，参考任务内容即可，不要提及\"定时任务\"\"工具\"等技术细节。\n"
    "4. 当前时间：{{current_time}}；需要完成的事情：{{content}}。\n"
    "任务信息：{{job_json}}"
)


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
    def from_dict(cls, data: dict) -> "TaskEntry":
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


class TaskScheduler:
    """定时任务管理器（每 Bot 实例一个）。"""

    def __init__(self, module, data_dir: str | None = None) -> None:
        self.module = module
        self.bot = module.ctx.bot
        self.bot_id = module.bot_id
        self.task_manager = module.ctx.services.task_manager
        self.session_mgr = SessionManager(str(module.bot_id))
        self._tasks: dict[str, TaskEntry] = {}
        self._timers: dict[str, asyncio.Task] = {}

        if data_dir is None:
            data_dir = llm_data_dir()
        # 每个账号一个目录：data/llm/<bot_id>/tasks_data.json
        self._dir = os.path.join(data_dir, safe_bot_id(module.bot_id))
        os.makedirs(self._dir, exist_ok=True)
        self._file = os.path.join(self._dir, "tasks_data.json")
        # 兼容：旧版扁平文件 data/llm/tasks_data_<bot>.json（迁移期回退读取）
        self._legacy_file = os.path.join(data_dir, f"tasks_data_{module.bot_id}.json")
        self._load()
        self._restore()

    # ── 配置 ─────────────────────────────────────────────
    def _enabled(self) -> bool:
        return bool(self.module.config.get("schedule_enable", True))

    def _owner(self) -> str:
        return f"agent:{self.module.bot_id}"

    # ── 调度入口 ─────────────────────────────────────────
    async def schedule(self, session_id: str, spec: dict) -> TaskEntry | None:
        """根据 LLM 提取的任务 spec（trigger/content/repeat）创建定时任务。

        session_id 形如 group_123 / private_456，据此推导私聊/群聊，
        避免调用方传错 is_group/is_private 导致任务发错目标。
        """
        if not self._enabled():
            return None
        trigger_expr = (spec.get("trigger") or "").strip()
        content = (spec.get("content") or "").strip()
        if not trigger_expr or not content:
            logger.add_info(f"#{self.bot_id}").warning(f"[定时任务] 任务信息不完整: {spec}")
            return None
        parsed = parse_schedule(trigger_expr)
        if parsed is None:
            logger.add_info(f"#{self.bot_id}").warning(f"[定时任务] 无法解析时间表达式: {trigger_expr}")
            return None

        repeat = parsed["repeat"]
        override = (spec.get("repeat") or "").strip().lower()
        if override in ("once", "daily", "weekly", "monthly", "interval"):
            repeat = override

        is_group = session_id.startswith("group_")
        target = session_id[len("group_"):] if is_group else (
            session_id[len("private_"):] if session_id.startswith("private_") else session_id
        )

        entry = TaskEntry(
            task_id=os.urandom(6).hex(),
            session_id=session_id,
            is_group=is_group,
            target=target,
            trigger_expr=trigger_expr,
            content=content,
            repeat=repeat,
            next_at=parsed["next_at"],
            weekday=parsed.get("weekday"),
            dom=parsed.get("dom"),
            interval_seconds=parsed.get("interval_seconds"),
        )
        self._tasks[entry.id] = entry
        try:
            self._start(entry)
            self._save()
        except Exception:
            # 启动失败 → 回滚，避免留下孤儿任务
            self._tasks.pop(entry.id, None)
            raise
        logger.add_info(f"#{self.bot_id}").info(
            f"[定时任务] 已创建 {entry.id} | {session_id} | {trigger_expr} -> "
            f"{entry.next_at:%Y-%m-%d %H:%M:%S} ({repeat}) | {content[:40]}"
        )
        return entry

    # ── 触发 ─────────────────────────────────────────────
    async def trigger_now(self, task_id: str) -> bool:
        """立即触发一次。一次性任务触发后结束；周期任务推进到下一触发时间。"""
        entry = self._tasks.get(task_id)
        if not entry or not entry.active:
            return False
        self._cancel_timer(task_id)
        await self._fire(entry)
        if entry.repeat == "once":
            self._complete(entry)
        else:
            entry.next_at = advance_repeat(
                entry.next_at, repeat=entry.repeat,
                weekday=entry.weekday, dom=entry.dom, interval_seconds=entry.interval_seconds,
            )
            self._start(entry)
            self._save()
        return True

    def cancel(self, task_id: str) -> bool:
        entry = self._tasks.pop(task_id, None)
        if not entry:
            return False
        entry.active = False
        self._cancel_timer(task_id)
        self._save()
        logger.add_info(f"#{self.bot_id}").info(f"[定时任务] 已取消 {task_id} | {entry.session_id}")
        return True

    # ── 定时循环 ─────────────────────────────────────────
    def _start(self, entry: TaskEntry) -> None:
        self._cancel_timer(entry.id)
        task = self.task_manager.create_task(
            self._run(entry),
            name=f"schedtask:{entry.id}",
            owner=self._owner(),
        )
        self._timers[entry.id] = task

    def _cancel_timer(self, task_id: str) -> None:
        task = self._timers.pop(task_id, None)
        if task and not task.done():
            task.cancel()

    async def _run(self, entry: TaskEntry) -> None:
        try:
            while entry.active:
                delay = entry.next_at.timestamp() - time.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                if not entry.active:
                    return
                await self._fire(entry)
                if not entry.active:
                    return
                if entry.repeat == "once":
                    self._complete(entry)
                    return
                entry.next_at = advance_repeat(
                    entry.next_at, repeat=entry.repeat,
                    weekday=entry.weekday, dom=entry.dom, interval_seconds=entry.interval_seconds,
                )
                self._save()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.add_info(f"#{self.bot_id}").error(f"[定时任务] 任务 {entry.id} 循环异常: {e}")

    async def _fire(self, entry: TaskEntry) -> None:
        """定时触发：像主动消息一样做一次带系统提示词的 LLM 请求后自然发言。

        LLM 生成失败/无 key 时兜底发送任务自带的固定内容。
        """
        entry.fired_count += 1
        if self.bot is None:
            logger.add_info(f"#{self.bot_id}").warning(f"[定时任务] 无可用 Bot，跳过发送 {entry.id}")
            return

        config = self.module.config
        # 定时任务也支持流式：与普通消息使用同一套流式发送配置
        if config.get("stream_output", False) and config.get("stream_scheduled_enabled", False):
            session = self.session_mgr.get_session(entry.session_id)
            if session is None:
                session = self.session_mgr.create_session(
                    entry.session_id,
                    "group" if entry.is_group else "private",
                    int(config.get("session_timeout", 60)),
                )
                await asyncio.to_thread(self.session_mgr.restore_session_from_archive, session, entry.session_id)

            from app.llm.initiative_stream import stream_send_initiative

            messages = await self._build_messages(entry)
            try:
                full_text = await stream_send_initiative(
                    self.module,
                    self.bot,
                    entry.session_id,
                    entry.is_group,
                    entry.target,
                    messages,
                    model=config.get("model", "deepseek-chat"),
                    temperature=config.get("temperature", 0.7),
                    max_tokens=config.get("max_tokens", 1024),
                )
            except Exception as e:
                logger.add_info(f"#{self.bot_id}").error(f"[定时任务] 流式生成异常，改用固定内容: {e}")
                full_text = ""

            clean = strip_all_tags(full_text).strip()
            if not clean:
                clean = entry.content
                try:
                    if entry.is_group:
                        await self.bot.send_group_msg(group_id=int(entry.target), message=clean)
                    else:
                        await self.bot.send_private_msg(user_id=int(entry.target), message=clean)
                except Exception as e:
                    logger.add_info(f"#{self.bot_id}").error(f"[定时任务] 发送失败 {entry.id} -> {entry.session_id}: {e}")
            else:
                logger.add_info(f"#{self.bot_id}").info(
                    f"[定时任务] 流式触发 {entry.id} -> {entry.session_id}: {clean[:50]}"
                )

            if session:
                self.session_mgr.add_message(entry.session_id, "assistant", clean)
                await asyncio.to_thread(self.session_mgr.history.save_session, session)
            self._save()
            return

        try:
            resp = await self._generate_reply(entry)
        except Exception as e:
            logger.add_info(f"#{self.bot_id}").error(f"[定时任务] LLM 生成异常，改用固定内容: {e}")
            resp = None

        if resp is not None and resp.ok:
            text = resp.text
        else:
            logger.add_info(f"#{self.bot_id}").warning(f"[定时任务] LLM 生成失败，发送固定内容: {entry.content}")
            text = entry.content

        # 防御：剥离角色提示词可能输出的 <type=...> 标签
        clean = strip_all_tags(text)
        if not clean.strip():
            clean = entry.content

        session = self.session_mgr.get_session(entry.session_id)

        try:
            if entry.is_group:
                await self.bot.send_group_msg(group_id=int(entry.target), message=clean)
            else:
                await self.bot.send_private_msg(user_id=int(entry.target), message=clean)
            logger.add_info(f"#{self.bot_id}").info(
                f"[定时任务] 触发 {entry.id} -> {entry.session_id}: {clean[:50]}"
            )
            if session:
                self.session_mgr.add_message(entry.session_id, "assistant", clean)
                await asyncio.to_thread(self.session_mgr.history.save_session, session)
        except Exception as e:
            logger.add_info(f"#{self.bot_id}").error(f"[定时任务] 发送失败 {entry.id} -> {entry.session_id}: {e}")
        self._save()

    async def _build_messages(self, entry: TaskEntry) -> list[dict]:
        """构建定时任务触发的 LLM 消息。"""
        session = self.session_mgr.get_session(entry.session_id)
        config = self.module.config
        history = self.session_mgr.get_history(
            entry.session_id, limit=int(config.get("history_rounds", 50))
        ) if session else []
        history = format_history_for_llm(history, is_private=not entry.is_group)
        system_prompt = config.get("system_prompt", "你是一个友好的助手。")
        now_str = datetime.now().strftime("%Y年%m月%d日 %H:%M")
        job_json = json.dumps({
            "id": entry.id[:8],
            "repeat": entry.repeat,
            "note": entry.content,
            "session": entry.session_id,
            "fired_count": entry.fired_count,
        }, ensure_ascii=False)
        prompt_tpl = config.get("schedule_prompt", DEFAULT_SCHEDULE_PROMPT)
        user_prompt = (
            prompt_tpl.replace("{{content}}", entry.content)
            .replace("{{current_time}}", now_str)
            .replace("{{job_json}}", job_json)
        )

        pre_history_text = ""
        if entry.is_group and config.get("include_pre_history", False):
            # 与普通回复一致：定时任务也只有 include_pre_history 开启时才拉在线群聊记录作为背景，
            # 不会把非 @ 群消息写入会话历史。
            history_text = await fetch_group_online_history(
                self.bot,
                entry.target,
                count=int(config.get("history_rounds", 50)),
                self_ids={str(self.bot_id), str(getattr(self.bot, "bot_id", "") or "")},
            )
            if history_text:
                group_name = await fetch_group_name(self.bot, entry.target)
                pre_history_text = build_group_env_text(
                    group_id=entry.target,
                    group_name=group_name,
                    history_text=history_text,
                    current_time=now_str,
                )

        return build_messages(
            system_prompt=system_prompt,
            pre_history_text=pre_history_text,
            history=history,
            user_text=user_prompt,
            with_schedule_instruction=False,
        )

    async def _generate_reply(self, entry: TaskEntry):
        """构建并执行一次带系统提示词的 LLM 请求（会话已过期则从归档恢复上下文）。"""
        session = self.session_mgr.get_session(entry.session_id)
        if session is None:
            session = self.session_mgr.create_session(
                entry.session_id,
                "group" if entry.is_group else "private",
                int(self.module.config.get("session_timeout", 60)),
            )
            await asyncio.to_thread(self.session_mgr.restore_session_from_archive, session, entry.session_id)

        messages = await self._build_messages(entry)
        if hasattr(self.module.config, "set_session"):
            self.module.config.set_session(entry.session_id)
        try:
            if hasattr(self.module, "provider_chain"):
                chain = self.module.provider_chain()
                if chain:
                    return await chat_with_fallback(
                        chain,
                        messages,
                        model=self.module.config.get("model", "deepseek-chat"),
                        temperature=self.module.config.get("temperature", 0.7),
                        max_tokens=self.module.config.get("max_tokens", 1024),
                    )
            # 兼容旧模块/测试：直接走模块自己的 get_provider
            provider = get_provider(dict(self.module.config.raw_config))
            return await provider.chat(
                messages,
                model=self.module.config.get("model", "deepseek-chat"),
                temperature=self.module.config.get("temperature", 0.7),
                max_tokens=self.module.config.get("max_tokens", 1024),
            )
        finally:
            if hasattr(self.module.config, "clear_session"):
                self.module.config.clear_session()

    def _complete(self, entry: TaskEntry) -> None:
        self._tasks.pop(entry.id, None)
        self._timers.pop(entry.id, None)
        entry.active = False
        self._save()

    # ── 管理接口 ─────────────────────────────────────────
    def status(self) -> list[dict]:
        rows = []
        for e in self._tasks.values():
            rows.append({
                "task_id": e.id,
                "session_id": e.session_id,
                "target": e.target,
                "type": "group" if e.is_group else "private",
                "repeat": e.repeat,
                "trigger_expr": e.trigger_expr,
                "content": e.content,
                "next_trigger_time": int(e.next_at.timestamp()),
                "fired_count": e.fired_count,
                "active": e.active,
                "created_at": e.created_at,
            })
        return sorted(rows, key=lambda r: r["next_trigger_time"])

    def count(self) -> int:
        return len(self._tasks)

    def stop(self) -> None:
        """取消全部定时器（任务数据保留在磁盘，下次加载恢复）。"""
        for task_id in list(self._timers.keys()):
            self._cancel_timer(task_id)

    # ── 持久化 ───────────────────────────────────────────
    def _load(self) -> None:
        try:
            # 优先新目录文件；不存在则回退旧扁平文件（迁移期兼容）
            file_path = self._file if os.path.exists(self._file) else self._legacy_file
            if not os.path.exists(file_path):
                return
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            items = data.get("tasks", []) if isinstance(data, dict) else data
            for item in items or []:
                try:
                    entry = TaskEntry.from_dict(item)
                    # 以 session_id 为准校正私聊/群聊（防止历史版本 is_group 存反导致发错目标）
                    if entry.session_id.startswith("group_"):
                        entry.is_group = True
                    elif entry.session_id.startswith("private_"):
                        entry.is_group = False
                    if entry.active:
                        self._tasks[entry.id] = entry
                except Exception:
                    continue
        except Exception as e:
            logger.add_info(f"#{self.bot_id}").warning(f"[定时任务] 加载状态失败: {e}")

    def _save(self) -> None:
        try:
            with open(self._file, "w", encoding="utf-8") as f:
                json.dump(
                    {"tasks": [t.to_dict() for t in self._tasks.values()]},
                    f, ensure_ascii=False, indent=2,
                )
        except Exception as e:
            logger.add_info(f"#{self.bot_id}").warning(f"[定时任务] 保存状态失败: {e}")

    def _restore(self) -> None:
        """启动恢复：移除过期的一次性任务，周期任务推进到未来，重新武装计时器。"""
        now_ts = time.time()
        removed = 0
        advanced = 0
        armed = 0
        for entry in list(self._tasks.values()):
            if not entry.active:
                continue
            if entry.next_at.timestamp() <= now_ts:
                if entry.repeat == "once":
                    # 一次性任务已过期 → 移除
                    self._tasks.pop(entry.id, None)
                    removed += 1
                    continue
                # 周期任务 → 推进到未来
                advanced += 1
                while entry.next_at.timestamp() <= now_ts:
                    entry.next_at = advance_repeat(
                        entry.next_at, repeat=entry.repeat,
                        weekday=entry.weekday, dom=entry.dom, interval_seconds=entry.interval_seconds,
                    )
            self._start(entry)
            armed += 1
        self._save()
        if removed or advanced:
            logger.add_info(f"#{self.bot_id}").info(
                f"[定时任务] 启动恢复: 移除过期 {removed} 个，周期任务推进 {advanced} 个，武装 {armed} 个"
            )


# ==================== schedule_task 工具（原生 function calling） ====================

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
        entry = await scheduler.schedule(session_id, {"trigger": trigger, "content": note})
        if entry is None:
            logger.add_info(f"#{bot_id}").warning(f"[定时任务] 工具 create 失败: trigger={trigger}")
            return (
                f"error: 无法解析时间表达式 {trigger!r}。支持格式：明天早上8点 / 今晚10点 / "
                "每天早上8点 / 每周五下午6点 / 每月1号上午9点 / 5分钟后 / 半小时后 / 每30分钟 / 08:30。"
                "请用这些格式重新尝试。"
            )
        logger.add_info(f"#{bot_id}").info(
            f"[定时任务] 工具 create -> {session_id}: {trigger} @ {entry.next_at:%Y-%m-%d %H:%M} ({entry.repeat})"
        )
        return (
            f"success: 已创建定时任务，id={entry.id[:8]}，重复方式={entry.repeat}，"
            f"下次触发时间={entry.next_at:%Y-%m-%d %H:%M:%S}。"
        )

    if action == "list":
        rows = [t for t in scheduler.status() if t["session_id"] == session_id]
        if not rows:
            return "当前会话没有定时任务"
        lines = []
        for t in rows:
            next_s = datetime.fromtimestamp(t["next_trigger_time"]).strftime("%Y-%m-%d %H:%M")
            lines.append(f"id={t['task_id'][:8]} | {t['repeat']} | 下次 {next_s} | {t['content'][:30]}")
        return "本会话定时任务:\n" + "\n".join(lines)

    if action == "delete":
        job_id = str(args.get("job_id") or "").strip()
        if not job_id:
            return "error: delete 需要提供 job_id"
        task = next(
            (t for t in scheduler.status() if t["task_id"] == job_id and t["session_id"] == session_id),
            None,
        )
        if task is None:
            return f"error: 未找到本会话的任务 {job_id}（用 list 查看）"
        if scheduler.cancel(job_id):
            return f"success: 已删除定时任务 {job_id[:8]}"
        return f"error: 删除任务 {job_id} 失败"

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
