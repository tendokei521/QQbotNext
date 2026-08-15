"""主动消息能力（框架级，化用 astrbot_plugin_proactive_chat 核心逻辑）。

- 私聊：用户回复后在随机间隔 [min,max] 内由 LLM 主动发言；未回复计数递增，用户回复重置；
- 群聊：沉默 idle_minutes 后由 LLM 主动开口；Bot 发言重置沉默计时器；
- 免打扰时段跳过；未回复达到上限停止；LLM 生成期间用户来消息则丢弃本次主动消息；
- 状态持久化到 llm 数据目录 proactive_data.json，重启恢复下一触发时间。
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
from datetime import datetime
from typing import Any

from app.llm import logger, llm_data_dir
from app.llm.prompt import build_messages
from app.llm.providers import get_provider
from app.llm.session import SessionManager
from app.llm.tags import strip_all_tags

DEFAULT_PROACTIVE_PROMPT = (
    "你现在要发起一次主动消息，像真人一样自然开口。\n"
    "当前时间：{{current_time}}；之前已主动发言但无人接话的次数：{{unanswered_count}}。\n"
    "结合最近对话，自然地说一句适合此刻的话（不刷屏、不机械、不过度主动）。"
)


def _session_parts(session_id: str) -> tuple[str, str]:
    """返回 (is_group, target_id)。session_id 形如 group_123 / private_456。"""
    if session_id.startswith("group_"):
        return True, session_id[len("group_"):]
    if session_id.startswith("private_"):
        return False, session_id[len("private_"):]
    return False, session_id


class ProactiveManager:
    """主动消息管理器（每 Bot 实例一个）。"""

    def __init__(self, module) -> None:
        self.module = module
        self.bot = module.ctx.bot
        self.task_manager = module.ctx.services.task_manager
        self.session_mgr = SessionManager(str(module.bot_id))
        self._timers: dict[str, asyncio.Task] = {}       # 私聊下次主动任务
        self._group_timers: dict[str, asyncio.Task] = {}  # 群聊沉默计时器
        self._data: dict[str, dict] = {}
        self._file = os.path.join(llm_data_dir(), "proactive_data.json")
        self._load()
        self._restore()

    # ── 配置读取 ─────────────────────────────────────────
    def _cfg(self, key: str, default: Any = None) -> Any:
        return self.module.config.get(f"proactive_{key}", default)

    def _session_enabled(self, session_id: str, is_group: bool) -> bool:
        if not self._cfg("friend_enable", False) and not self._cfg("group_enable", False):
            return False
        sessions = self._cfg("group_sessions", []) if is_group else self._cfg("friend_sessions", [])
        _, target = _session_parts(session_id)
        return target in [str(s) for s in sessions]

    def _is_quiet_time(self) -> bool:
        start = int(self._cfg("quiet_hours_start", 1))
        end = int(self._cfg("quiet_hours_end", 7))
        now_h = datetime.now().hour
        if start == end:
            return False
        if start < end:
            return start <= now_h < end
        return now_h >= start or now_h < end  # 跨天

    # ── 事件入口 ─────────────────────────────────────────
    async def on_message(self, session_id: str, is_group: bool, is_self: bool) -> None:
        """消息事件观察：记录活跃时间；非自身消息重置计数并重新调度。"""
        self._data.setdefault(session_id, {})["last_user_time"] = time.time()
        if is_self:
            return
        if not self._session_enabled(session_id, is_group):
            return
        self._data.setdefault(session_id, {})["unanswered_count"] = 0
        self._save()
        if is_group:
            self._reset_group_silence(session_id)
        else:
            self._schedule_next_private(session_id)

    async def on_bot_sent(self, session_id: str, is_group: bool) -> None:
        """Bot 发言后重置群聊沉默计时器。"""
        if is_group and self._session_enabled(session_id, is_group):
            self._reset_group_silence(session_id)

    # ── 调度 ─────────────────────────────────────────────
    def _owner(self) -> str:
        return f"agent:{self.module.bot_id}"

    def _schedule_next_private(self, session_id: str) -> None:
        self._cancel(self._timers, session_id)
        min_iv = int(self._cfg("min_interval_minutes", 30)) * 60
        max_iv = max(min_iv, int(self._cfg("max_interval_minutes", 900)) * 60)
        delay = random.randint(min_iv, max_iv)
        self._data.setdefault(session_id, {})["next_trigger_time"] = time.time() + delay
        self._save()
        task = self.task_manager.create_task(
            self._delayed_check(session_id, delay),
            name=f"proactive:{session_id}",
            owner=self._owner(),
        )
        self._timers[session_id] = task
        logger.add_info(f"#{self.module.bot_id}").debug(f"[主动消息] {session_id} 安排 {delay}s 后主动发言")

    def _reset_group_silence(self, session_id: str) -> None:
        self._cancel(self._group_timers, session_id)
        idle = int(self._cfg("group_idle_minutes", 10)) * 60
        task = self.task_manager.create_task(
            self._delayed_check(session_id, idle),
            name=f"silence:{session_id}",
            owner=self._owner(),
        )
        self._group_timers[session_id] = task

    def _cancel(self, pool: dict, session_id: str) -> None:
        task = pool.pop(session_id, None)
        if task and not task.done():
            task.cancel()

    async def _delayed_check(self, session_id: str, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        await self._check_and_chat(session_id)

    # ── 主动聊天核心 ─────────────────────────────────────
    async def _check_and_chat(self, session_id: str) -> None:
        self._timers.pop(session_id, None)
        self._group_timers.pop(session_id, None)
        is_group, target = _session_parts(session_id)
        if not self._session_enabled(session_id, is_group):
            return
        if self._is_quiet_time():
            logger.add_info(f"#{self.module.bot_id}").debug(f"[主动消息] {session_id} 处于免打扰时段，跳过")
            if not is_group:
                self._schedule_next_private(session_id)
            return
        unanswered = self._data.get(session_id, {}).get("unanswered_count", 0)
        max_unanswered = int(self._cfg("max_unanswered", 3))
        if max_unanswered > 0 and unanswered >= max_unanswered:
            return

        # 上下文
        session = self.session_mgr.get_session(session_id)
        history = self.session_mgr.get_history(session_id, limit=int(self.module.config.get("history_rounds", 50))) if session else []
        system_prompt = self.module.config.get("system_prompt", "你是一个友好的助手。")
        now_str = datetime.now().strftime("%Y年%m月%d日 %H:%M")
        prompt_tpl = self._cfg("proactive_prompt", DEFAULT_PROACTIVE_PROMPT)
        user_prompt = prompt_tpl.replace("{{unanswered_count}}", str(unanswered)).replace("{{current_time}}", now_str)

        messages = build_messages(
            system_prompt=system_prompt,
            history=history,
            user_text=user_prompt,
            with_schedule_instruction=False,
        )

        # 生成期间新消息检查
        start_last = self._data.get(session_id, {}).get("last_user_time", 0)
        config = self.module.config

        # 主动消息也支持流式：与普通消息使用同一套流式发送配置
        if config.get("stream_output", False) and config.get("stream_proactive_enabled", False):
            from app.llm.initiative_stream import stream_send_initiative

            full_text = await stream_send_initiative(
                self.module,
                self.bot,
                session_id,
                is_group,
                target,
                messages,
                model=config.get("model", "deepseek-chat"),
                temperature=config.get("temperature", 0.7),
                max_tokens=config.get("max_tokens", 1024),
            )
            clean = strip_all_tags(full_text).strip()
            if not clean:
                if not is_group:
                    self._schedule_next_private(session_id)
                return
            if session:
                self.session_mgr.add_message(session_id, "assistant", clean)
                await asyncio.to_thread(self.session_mgr.history.save_session, session)
            self._data.setdefault(session_id, {})["unanswered_count"] = unanswered + 1
            self._save()
            logger.add_info(f"#{self.module.bot_id}").info(
                f"[主动消息] {session_id} 流式主动发言完成（未回复 {unanswered + 1} 次）"
            )
            if not is_group:
                self._schedule_next_private(session_id)
            return

        provider = get_provider(dict(config.raw_config))
        resp = await provider.chat(
            messages,
            model=self.module.config.get("model", "deepseek-chat"),
            temperature=self.module.config.get("temperature", 0.7),
            max_tokens=self.module.config.get("max_tokens", 1024),
        )
        if self._data.get(session_id, {}).get("last_user_time", 0) != start_last:
            logger.add_info(f"#{self.module.bot_id}").info(f"[主动消息] {session_id} 生成期间用户来消息，丢弃本次")
            return
        if not resp.ok:
            if not is_group:
                self._schedule_next_private(session_id)
            return

        # 发送 + 存档 + 计数（发送前剥离可能的 <type=...> 标签）
        clean = strip_all_tags(resp.text)
        try:
            if is_group:
                await self.bot.send_group_msg(group_id=int(target), message=clean)
            else:
                await self.bot.send_private_msg(user_id=int(target), message=clean)
        except Exception as e:
            logger.add_info(f"#{self.module.bot_id}").error(f"[主动消息] 发送失败: {e}")
            return

        if session:
            self.session_mgr.add_message(session_id, "assistant", clean)
            await asyncio.to_thread(self.session_mgr.history.save_session, session)
        self._data.setdefault(session_id, {})["unanswered_count"] = unanswered + 1
        self._save()
        logger.add_info(f"#{self.module.bot_id}").info(f"[主动消息] {session_id} 主动发言完成（未回复 {unanswered + 1} 次）")
        if not is_group:
            self._schedule_next_private(session_id)

    # ── 持久化 / 清理 ────────────────────────────────────
    def _load(self) -> None:
        try:
            if os.path.exists(self._file):
                with open(self._file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._data = data
        except Exception as e:
            logger.add_info(f"#{self.module.bot_id}").warning(f"[主动消息] 加载状态失败: {e}")

    def _restore(self) -> None:
        """启动恢复：重排主动消息计时器。

        私聊：未过期的按原触发时间武装；已过期的重新随机间隔。
        群聊：重新武装沉默计时器。
        """
        restored = 0
        for session_id in list(self._data.keys()):
            is_group, _ = _session_parts(session_id)
            if not self._session_enabled(session_id, is_group):
                continue
            if is_group:
                self._reset_group_silence(session_id)
                restored += 1
                continue
            next_ts = self._data.get(session_id, {}).get("next_trigger_time")
            if next_ts and next_ts > time.time():
                # 未过期 → 按原触发时间重新武装
                delay = next_ts - time.time()
                task = self.task_manager.create_task(
                    self._delayed_check(session_id, delay),
                    name=f"proactive:{session_id}",
                    owner=self._owner(),
                )
                self._timers[session_id] = task
                restored += 1
            else:
                # 已过期 / 缺失 → 重新随机间隔
                self._schedule_next_private(session_id)
                restored += 1
        if restored:
            logger.add_info(f"#{self.module.bot_id}").info(
                f"[主动消息] 启动恢复 {restored} 个会话计时器"
            )

    def _save(self) -> None:
        try:
            with open(self._file, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.add_info(f"#{self.module.bot_id}").warning(f"[主动消息] 保存状态失败: {e}")

    def stop(self) -> None:
        for session_id in list(self._timers.keys()):
            self._cancel(self._timers, session_id)
        for session_id in list(self._group_timers.keys()):
            self._cancel(self._group_timers, session_id)

    # ── 管理接口 ─────────────────────────────────────────
    def status(self) -> list[dict]:
        """每会话状态：启用、类型、下次触发、未回复数、计时器类型。"""
        rows = []
        friend_sessions = [str(s) for s in self._cfg("friend_sessions", [])]
        group_sessions = [str(s) for s in self._cfg("group_sessions", [])]
        seen = set()
        for sid, is_group, target in (
            [(f"private_{s}", False, s) for s in friend_sessions]
            + [(f"group_{s}", True, s) for s in group_sessions]
        ):
            seen.add(sid)
            data = self._data.get(sid, {})
            next_ts = data.get("next_trigger_time")
            rows.append({
                "session_id": sid,
                "target": target,
                "type": "group" if is_group else "private",
                "enabled": self._session_enabled(sid, is_group),
                "unanswered": data.get("unanswered_count", 0),
                "last_user_time": data.get("last_user_time"),
                "next_trigger_time": next_ts,
                "timer": "private" if sid in self._timers else ("silence" if sid in self._group_timers else ""),
            })
        # 补充运行中但不在配置列表的会话（历史残留）
        all_ids = set(self._data.keys()) | set(self._timers.keys()) | set(self._group_timers.keys())
        for sid in all_ids:
            if sid in seen:
                continue
            is_group, target = _session_parts(sid)
            data = self._data.get(sid, {})
            rows.append({
                "session_id": sid,
                "target": target,
                "type": "group" if is_group else "private",
                "enabled": self._session_enabled(sid, is_group),
                "unanswered": data.get("unanswered_count", 0),
                "last_user_time": data.get("last_user_time"),
                "next_trigger_time": data.get("next_trigger_time"),
                "timer": "private" if sid in self._timers else ("silence" if sid in self._group_timers else ""),
            })
        return rows

    async def manual_trigger(self, session_id: str) -> bool:
        """手动立即触发一次主动消息。"""
        is_group, _ = _session_parts(session_id)
        if not self._session_enabled(session_id, is_group):
            return False
        self._timers.pop(session_id, None)
        self._group_timers.pop(session_id, None)
        await self._check_and_chat(session_id)
        return True
