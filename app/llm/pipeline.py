"""LLM 流水线：模块流水线之后的后台异步处理。

职责：
- 接收 AgentNode 提交的 LLM Job；
- 依次执行 pre_request → LLM 请求 → post_response → pre_send → send → post_send；
- 通过 LlmPool 支持防抖 / 合并 / 取代；
- 不阻塞模块流水线的单 Bot Worker。
"""

from __future__ import annotations

import asyncio
import re
import uuid
from typing import Any

from app.domain.message import Message
from app.infrastructure.cache import get_cache
from app.llm.context import LlmContext, LlmJob
from app.llm.pool import LlmPool


class LlmPipeline:
    def __init__(self, runtime: Any, task_manager: Any = None) -> None:
        self.runtime = runtime
        self.task_manager = task_manager
        self.pool = LlmPool()
        self._tasks: set[asyncio.Task] = set()

    def submit(self, event) -> asyncio.Task | None:
        """提交一个 LLM 处理任务（非阻塞）。"""
        if event.bot is None:
            return None

        is_group = event.event_type == "message_group"
        session_id = f"group_{event.group.group_id}" if is_group else f"private_{event.user_id}"
        user_text = event.text.strip()

        ctx = LlmContext(
            event=event,
            runtime=self.runtime,
            bot=event.bot,
            session_id=session_id,
            user_text=user_text,
        )
        job = LlmJob(id=uuid.uuid4().hex, group_key=session_id, ctx=ctx)
        ctx.job = job
        event._llm_job = job

        task = asyncio.create_task(self._run(job), name=f"llm_pipeline:{job.id[:8]}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # ==================== 框架级用户感知 ====================

    def _raw_user_text(self, event) -> str:
        raw = getattr(event, "raw_message", "") or ""
        if raw:
            return re.sub(r"\[CQ:[^\]]*\]", "", raw).strip()
        return (getattr(event, "text", "") or "").strip()

    async def _collect_user_context(self, ctx: LlmContext) -> None:
        config = self.runtime.config
        if not config.get("context_enable", True):
            return

        event = ctx.event
        info = {
            "sender": None,
            "mentioned": [],
            "quote": None,
            "quote_sender": None,
            "sent_text": self._raw_user_text(event),
        }

        if event.event_type == "message_group":
            if config.get("include_sender", True):
                user = getattr(event, "user", None)
                nickname = (getattr(user, "card", "") or getattr(user, "nickname", "") or "")
                info["sender"] = f"{nickname}({event.user_id})" if nickname else str(event.user_id)
            if config.get("include_mentioned", True):
                info["mentioned"] = await self._collect_at_info(ctx)

        if config.get("include_quote", True):
            quote = await self._collect_quote_info(ctx)
            if quote:
                info["quote"] = quote["text"]
                info["quote_sender"] = f"{quote['sender_nickname']}({quote['sender_id']})"

        ctx.state["user_context"] = info

    async def _format_user_context(self, ctx: LlmContext) -> None:
        info = ctx.state.get("user_context")
        if not info:
            return

        config = self.runtime.config
        event = ctx.event
        is_group = event.event_type == "message_group"
        parts: list[str] = []

        if is_group:
            if config.get("include_sender", True) and info.get("sender"):
                parts.append(f"发送者：{info['sender']}")
            if config.get("include_mentioned", True) and info.get("mentioned"):
                parts.append("提到了(用户名)：" + "、".join(info["mentioned"]))

        if config.get("include_quote", True) and info.get("quote"):
            if is_group and config.get("include_quote_sender", True) and info.get("quote_sender"):
                parts.append(f"引用了：{info['quote_sender']}发送的引用消息：“{info['quote']}”")
            else:
                parts.append(f"引用了：{info['quote']}")

        sent_text = ctx.user_text.strip() or (info.get("sent_text") or "").strip()
        if config.get("include_sent", True) and sent_text:
            parts.append(f"发送了：{sent_text}")

        if parts:
            ctx.user_text = "\n".join(parts)

    async def _collect_at_info(self, ctx: LlmContext) -> list[str]:
        event = ctx.event
        if not event.message:
            return []

        result: list[str] = []
        config = self.runtime.config
        for seg in event.message:
            if seg.type != "at":
                continue
            qq = str(seg.data.get("qq", "") or "")
            if not qq:
                continue
            if qq in (str(event.self_id), str(getattr(event, "bot_id", "") or "")):
                continue
            if qq in ("all", "0"):
                result.append("全体成员")
                continue

            nickname = qq
            if config.get("fetch_at_nickname", True):
                fetched = await self._fetch_group_member_nickname(ctx, qq)
                if fetched:
                    nickname = fetched
            result.append(f"{nickname}({qq})")
        return result

    async def _fetch_group_member_nickname(self, ctx: LlmContext, qq: str) -> str:
        event = ctx.event
        group_id = getattr(getattr(event, "group", None), "group_id", None)
        if not group_id or not event.bot:
            return ""

        cache = get_cache()
        cache_key = f"llm_enhance:nick:{event.bot_id}:{group_id}:{qq}"
        cached = cache.get(cache_key)
        if cached:
            return cached

        try:
            resp = await event.bot.get_group_member_info(group_id=group_id, user_id=int(qq))
            data = (resp or {}).get("data", {}) or {}
            nickname = data.get("card") or data.get("nickname") or ""
            if nickname:
                cache.set(cache_key, nickname, 600)
                return nickname
        except Exception:
            pass
        return ""

    async def _collect_quote_info(self, ctx: LlmContext) -> dict | None:
        event = ctx.event
        config = self.runtime.config
        if not event.bot or not config.get("fetch_quote_content", True):
            return None

        reply_id = None
        for seg in event.message:
            if seg.type == "reply":
                reply_id = str(seg.data.get("id", "") or "")
                break
        if not reply_id:
            return None

        try:
            resp = await event.bot.get_msg(reply_id)
            data = (resp or {}).get("data", {}) or {}
            sender = data.get("sender", {}) or {}
            sender_nickname = sender.get("card") or sender.get("nickname") or ""
            sender_id = sender.get("user_id", "")
            text = self._segments_to_text(data.get("message"))
            if not text:
                return None
            return {
                "text": text,
                "sender_nickname": sender_nickname or str(sender_id),
                "sender_id": sender_id,
            }
        except Exception:
            return None

    @staticmethod
    def _segments_to_text(message) -> str:
        if isinstance(message, str):
            return message
        msg = Message.from_onebot(message)
        text = msg.text
        if text:
            return text
        if msg.segments:
            return "[" + ",".join(s.type for s in msg.segments) + "]"
        return ""

    async def _run(self, job: LlmJob) -> None:
        ctx = job.ctx
        try:
            # #chat 指令仍然走原 chat.handle（它会自己发送回复）
            if ctx.user_text.startswith("#chat "):
                from app.llm.chat import handle as agent_handle

                await agent_handle(self.runtime, ctx.event)
                return

            # 群聊必须满足触发条件（@ 或关键词），否则不进入 LLM 流水线
            if ctx.event.event_type == "message_group":
                config = self.runtime.config
                from app.llm.trigger import check_trigger

                triggered, is_at, user_text = check_trigger(
                    ctx.event.message,
                    ctx.event.self_id,
                    config.get("trigger_at", False),
                    config.get("trigger_keyword", []),
                )
                if not triggered:
                    return

                if is_at:
                    import re

                    user_text = re.sub(r"\[CQ:at,qq=\d+\]", "", user_text).strip()
                    user_text = re.sub(r"@\S+\s*", "", user_text).strip()

                ctx.user_text = user_text.strip()
                if not ctx.user_text:
                    return

            config = self.runtime.config
            # 没有 key 或对应场景未启用时，不视为“触发 LLM”，不重置主动消息状态
            if not config.get("api_key", ""):
                return
            if ctx.event.event_type == "message_group" and not config.get("group_enable", False):
                return
            if ctx.event.event_type == "message_private":
                if not config.get("private_enable", True):
                    return
                if not ctx.user_text.strip():
                    return

            # 框架级用户感知：先收集上下文，等防抖/合并后再格式化
            await self._collect_user_context(ctx)

            # 1. 请求前钩子（可暂停/防抖/合并/跳过）
            if not await self._run_stage("pre_request", ctx):
                return
            if job.skip or job.superseded:
                return

            # 防抖/合并等钩子执行完后，再统一格式化用户上下文
            await self._format_user_context(ctx)

            # 只有真正进入 LLM 请求前才更新主动消息状态（群聊普通消息不重置沉默计时器）
            await self._observe(ctx)

            # 2. 实际 LLM 请求
            if self.runtime.config.get("stream_output", False):
                await self._run_stream(ctx)
                return

            from app.llm.chat import generate_response

            response_text = await generate_response(self.runtime, ctx.event, ctx)
            if response_text is None:
                return
            if job.skip or job.superseded:
                return

            ctx.response_text = response_text
            ctx.response_messages = [Message.from_text(response_text)]

            # 3. 请求后钩子（可拆分/改写）
            if not await self._run_stage("post_response", ctx):
                return
            if job.skip or job.superseded:
                return
            if not ctx.response_messages:
                ctx.response_messages = [Message.from_text(ctx.response_text)]
            # 统一为 Message，方便 pre_send / post_send 钩子安全读写
            ctx.response_messages = [
                m if isinstance(m, Message) else Message.from_text(str(m))
                for m in ctx.response_messages
            ]

            # 4. 发送阶段
            for msg in list(ctx.response_messages):
                if getattr(msg, "skip", False):
                    continue
                if not await self._run_stage("pre_send", ctx, msg):
                    return
                if getattr(msg, "skip", False):
                    continue

                await self._send(ctx, msg)

                await self._run_stage("post_send", ctx, msg)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            from app.core.logger import logger

            logger.add_info(f"#{self.runtime.bot_id}").exception(
                f"[LLM Pipeline] 任务异常: {e}"
            )
        finally:
            self.pool.finish(job)

    async def _run_stream(self, ctx: LlmContext) -> None:
        """流式路径：按句子发送，并触发 pre_send / post_send / post_stream 钩子。"""
        from app.llm.chat import stream_response

        config = self.runtime.config
        pool_enabled = config.get("stream_send_pool_enabled", False)

        if not pool_enabled:
            async for sentence in stream_response(self.runtime, ctx.event, ctx):
                msg = Message.from_text(sentence)

                if not await self._run_stage("pre_send", ctx, msg):
                    return
                if getattr(msg, "skip", False):
                    continue

                await self._send(ctx, msg)

                await self._run_stage("post_send", ctx, msg)

            await self._run_stage("post_stream", ctx)
            return

        from app.llm.send_pool import StreamSendPool

        async def send_message(msg: Message) -> None:
            await self._send(ctx, msg)

        async def pre_send_hook(msg: Message) -> bool:
            if not await self._run_stage("pre_send", ctx, msg):
                return True
            return getattr(msg, "skip", False)

        async def post_send_hook(msg: Message) -> None:
            await self._run_stage("post_send", ctx, msg)

        pool = StreamSendPool(
            config,
            send_message=send_message,
            pre_send=pre_send_hook,
            post_send=post_send_hook,
        )
        try:
            async for sentence in stream_response(self.runtime, ctx.event, ctx):
                await pool.put(Message.from_text(sentence))
            await pool.finish()
            await pool.wait_drained()
        finally:
            await pool.shutdown()

        await self._run_stage("post_stream", ctx)

    async def _run_stage(self, stage: str, ctx: LlmContext, msg: Any = None) -> bool:
        """执行某个 LLM 阶段的所有钩子。返回 False 表示应中止。"""
        hooks = self.runtime.llm_hooks.get(stage, ctx.event.event_type)
        for hook in hooks:
            try:
                if msg is None:
                    await hook.handler(ctx)
                else:
                    await hook.handler(ctx, msg)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                from app.core.logger import logger

                logger.add_info(f"#{self.runtime.bot_id}").exception(
                    f"[LLM Hook] {stage} 处理异常: {e}"
                )
            if ctx.job.skip or ctx.job.superseded:
                return False
        return True

    async def _send(self, ctx: LlmContext, msg: Message) -> None:
        if ctx.event.message_type == "private":
            await ctx.bot.send_private_msg(ctx.event.user_id, msg)
        else:
            await ctx.bot.send_group_msg(ctx.event.group.group_id, msg)

        pm = getattr(self.runtime, "proactive", None)
        if pm is not None:
            try:
                await pm.on_bot_sent(ctx.session_id, ctx.event.message_type != "private")
            except Exception:
                pass

    async def _observe(self, ctx: LlmContext) -> None:
        pm = getattr(self.runtime, "proactive", None)
        if pm is None:
            return
        is_group = ctx.event.event_type == "message_group"
        await pm.on_message(
            ctx.session_id,
            is_group,
            is_self=(ctx.event.user_id == ctx.event.self_id),
        )

    def cancel_for_module(self, module: Any) -> None:
        """取消该模块提交/关联的未完成任务（当前按模块钩子来源粗略处理）。"""
        # 运行中的任务难以安全取消具体模块，这里仅作为扩展点；
        # 模块卸载时 LlmHookRegistry.unregister_module 已保证新任务不再触发。
        return

    def shutdown(self) -> None:
        for task in list(self._tasks):
            if not task.done():
                task.cancel()
