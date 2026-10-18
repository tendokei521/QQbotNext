"""LLM 流水线：模块流水线之后的后台异步处理。

职责：
- 接收 AgentNode 提交的 LLM Job；
- 依次执行 pre_request → LLM 请求 → post_response → pre_send → send → post_send；
- 通过 LlmPool 支持防抖 / 合并 / 取代；
- 不阻塞模块流水线的单 Bot Worker。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app.domain.message import Message
from app.llm.actions import build_outbound_builder
from app.llm.commands import is_command
from app.llm.context import LlmContext, LlmJob
from app.llm.hooks import run_stage
from app.llm.locks import SessionLockManager
from app.llm.pool import LlmPool, merge_debounced_texts


class LlmPipeline:
    def __init__(self, runtime: Any, task_manager: Any = None) -> None:
        self.runtime = runtime
        self.task_manager = task_manager
        self.pool = LlmPool()
        self.session_locks = SessionLockManager()
        self._tasks: set[asyncio.Task] = set()
        self._session_generation: dict[str, int] = {}

    def submit(self, event) -> asyncio.Task | None:
        """提交一个 LLM 处理任务（非阻塞）。"""
        if event.bot is None:
            return None

        is_group = event.event_type == "message_group"
        session_id = f"group_{event.group.group_id}" if is_group else f"private_{event.user_id}"
        user_text = event.text.strip()

        # 回复打断：代际必须在**新消息提交时**抬高，否则打断失效。
        #
        # 历史坑（b86fc18）：代际自增曾被搬到 _run 里"确认真正触发 LLM 后"再执行，
        # 结果是任务自己抬高代际、再把自己认领为最新代际——于是
        # ① 旧任务在会话锁上排队时被打断，拿到锁后立刻"自我复活"，照样发请求；
        # ② 正在流式输出的任务被打断后只要再执行一次该赋值就复活，剩余句子继续发。
        # 两者都会表现为"开了打断还是一条条挨个回复"。
        #
        # 这里在提交时只对**确实会触发 LLM**的消息抬高代际（与 _run 的准入判定
        # 共用 _would_trigger_llm，避免两处漂移）：群聊里没被 @/未命中关键词的
        # 普通消息不会误打断，这正是 b86fc18 原本要防的问题。
        if self._would_trigger_llm(event, user_text, is_group):
            self.interrupt_session(session_id)
        generation = self._session_generation.get(session_id, 0)

        ctx = LlmContext(
            event=event,
            runtime=self.runtime,
            bot=event.bot,
            session_id=session_id,
            user_text=user_text,
        )
        job = LlmJob(id=uuid.uuid4().hex, group_key=session_id, ctx=ctx, generation=generation)
        ctx.job = job
        event._llm_job = job

        task = asyncio.create_task(self._run(job), name=f"llm_pipeline:{job.id[:8]}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def interrupt_session(self, session_id: str) -> None:
        """使某会话的旧任务过期（代际单调递增）。"""
        self._session_generation[session_id] = self._session_generation.get(session_id, 0) + 1

    def is_stale(self, job: LlmJob) -> bool:
        """判断任务是否已被同一会话的新消息打断。

        代际只增不减，因此用 ``>`` 而不是 ``!=``：任何"自己认领当前代际"的写法
        都不该能把一个已被取代的任务重新判定为新鲜（这正是打断失效的成因）。
        """
        return self._session_generation.get(job.group_key, 0) > job.generation

    def _adopt_generation_if_latest(self, job: LlmJob) -> bool:
        """任务进入 LLM 请求前的最后一道判定：我是否仍是最新任务？

        返回 ``True`` 表示可以把本轮代际认领为当前值并继续；
        返回 ``False`` 表示本任务在等待会话锁期间已被新消息取代，应当放弃。

        关键：只有"我的代际已经等于当前代际"才允许继续。若当前代际更高，
        说明已有更新的任务提交过，本任务**不得**把自己的代际改写成当前值——
        那会让 ``is_stale`` 永久失去判断力。
        """
        current = self._session_generation.get(job.group_key, 0)
        if current > job.generation:
            return False
        job.generation = current
        return True

    def _would_trigger_llm(self, event, user_text: str, is_group: bool) -> bool:
        """轻量判定：这条消息是否**真的会触发 LLM**（与 _run 的准入条件同源）。

        只覆盖与"消息本身"有关的准入：``#llm`` 指令走独立分支、群聊需 @/关键词、
        私聊非空文本或图片。会话配置（group_enable / private_enable / api_key）
        属于运行期判定，不在这里读取——提交时 ``config`` 的会话路由可能还没切换。

        用途：只有真触发的消息才允许打断同会话旧任务；普通群聊消息不打断。
        """
        if is_command(user_text):
            return False
        if not is_group:
            # 私聊：纯图片也算触发（图片由多模态链路承载）
            if user_text.strip():
                return True
            try:
                from app.llm.image import collect_image_segments

                return bool(collect_image_segments(event))
            except Exception:  # noqa: BLE001 —— 判定失败按"不打断"处理，保守优先
                return False

        config = self.runtime.config
        from app.llm.trigger import check_trigger

        try:
            triggered, _is_at, text = check_trigger(
                event.message,
                event.self_id,
                config.get("trigger_at", False),
                config.get("trigger_keyword", []),
            )
        except Exception:  # noqa: BLE001 —— 判定失败按"不打断"处理，保守优先
            return False
        return bool(triggered and (text.strip() or user_text.strip()))

    async def _resolve_images(self, ctx: LlmContext) -> list[dict]:
        """解析本轮消息的图片（仅当模型声明了 image 模态且开关打开）。

        放在 LLM 请求之前调用，好处是：拿不到可用 URL 的图片会先留下日志，
        而不是等 provider 返回 400 才发现；解析结果缓存在 ctx.state 供 chat 侧复用。
        """
        if not self.runtime.config.get("image_understanding_enable", True):
            return []
        try:
            from app.llm.providers.modalities import normalize_modalities, supports_image

            chain = self.runtime.provider_chain() if callable(
                getattr(self.runtime, "provider_chain", None)
            ) else []
            modalities = normalize_modalities((chain[0] or {}).get("modalities") if chain else None)
            if not supports_image(modalities):
                return []
            from app.llm.image import max_image_bytes, resolve_images

            return await resolve_images(
                ctx.event,
                bot=ctx.bot,
                max_images=int(self.runtime.config.get("image_max_count", 4) or 4),
                max_bytes=max_image_bytes(self.runtime.config),
                config=self.runtime.config,
            )
        except Exception as e:  # noqa: BLE001 —— 图片解析失败必须降级为纯文本，不能阻断对话
            from app.core.logger import logger

            logger.add_info(f"#{self.runtime.bot_id}").warning(f"图片解析失败，按纯文本处理: {e}")
            return []

    async def _run(self, job: LlmJob) -> None:
        ctx = job.ctx
        # 按会话切换配置档案（对齐 AstrBot UMO 路由）
        self.runtime.config.set_session(ctx.session_id)
        session_lock: asyncio.Lock | None = None
        lock_acquired = False
        try:
            # #llm 指令仍然走原 chat.handle（它会自己发送回复）
            if is_command(ctx.user_text):
                from app.llm.chat import handle as agent_handle

                await agent_handle(self.runtime, ctx.event)
                return

            # 图片段（本轮触发消息）：「@我 + 只发图」/私聊纯图片也应当进入 LLM
            from app.llm.image import collect_image_segments

            has_image = bool(collect_image_segments(ctx.event))
            if has_image:
                ctx.state["has_image"] = True

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
                    ctx.state["is_at"] = True
                    import re

                    user_text = re.sub(r"\[CQ:at,qq=\d+\]", "", user_text).strip()
                    user_text = re.sub(r"@\S+\s*", "", user_text).strip()

                ctx.user_text = user_text.strip()
                if not ctx.user_text and not has_image:
                    return

            config = self.runtime.config
            # 没有 key 或对应场景未启用时，不视为“触发 LLM”，不重置主动消息状态
            if not self.runtime.provider_config().get("api_key", ""):
                return
            if ctx.event.event_type == "message_group" and not config.get("group_enable", False):
                return
            if ctx.event.event_type == "message_private":
                if not config.get("private_enable", True):
                    return
                # 私聊纯图片同样放行（图片内容由多模态链路承载）
                if not ctx.user_text.strip() and not has_image:
                    return

            # 先截断原始用户消息，避免后续上下文包装后被 max_message_length 截掉“发送了”
            max_msg_len = int(config.get("max_message_length", 200) or 200)
            ctx.user_text = ctx.user_text[:max_msg_len]

            # 消息抖动：等静默 N 秒再请求，并把窗口内到达的多条消息合并进同一轮。
            #
            # 历史行为是 debounce=0（立即请求），于是用户连发四条消息会变成四次请求、
            # 四次回复——观感就是「一条一条挨个回」。开启抖动后由 LlmPool 只放行最新
            # Job，被取代的 Job 在这里返回 False 直接退出，它们的文本已攒在
            # _pending_texts 里，由存活 Job 取出合并。
            debounce = float(config.get("message_debounce_seconds", 0) or 0)
            max_wait = float(config.get("message_debounce_max_wait", 0) or 0) if debounce > 0 else 0.0
            if not await self.pool.wait_for_continue(ctx.job, debounce=debounce, max_wait=max_wait):
                return
            if debounce > 0:
                batch = self.pool.take_pending_texts(ctx.session_id)
                if len(batch) > 1:
                    ctx.user_text = merge_debounced_texts(batch, max_msg_len)
                    ctx.state["debounced_count"] = len(batch)

            # 1. 请求前钩子（可暂停/防抖/合并/跳过）
            if not await self._run_stage("pre_request", ctx):
                return
            if job.skip or job.superseded:
                return

            # 图片预解析：放在这里是为了「不可用的图片在整轮开销之前就暴露」，
            # 解析结果存 ctx.state，chat 侧直接复用（不重复解析/重复下载）。
            if has_image:
                ctx.state["user_images"] = await self._resolve_images(ctx)

            # 同一会话的 LLM 请求/历史写入串行化：新消息会先通过 LlmPool 标记旧任务 superseded，
            # 因此这里不会启动并发请求；旧任务释放后才能进入下一轮。
            session_lock = self.session_locks.lock(ctx.session_id)
            await session_lock.acquire()
            lock_acquired = True

            # 确认本轮真正会触发 LLM 后，再判定自己是否已被后来的消息取代（回复打断）。
            # 注意：这里**不能**重新抬高代际并认领（历史 b86fc18 的写法），
            # 否则在会话锁上排队的旧任务会"自我复活"，打断彻底失效。
            #
            # 位置在 #llm 指令分支之后：指令不产生流式回复、也不参与打断代际，
            # 不能被本判定顺手丢掉（否则新消息会把正在排队的指令顶掉）。
            if not self._adopt_generation_if_latest(ctx.job):
                return

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
            # 输出侧动作通道：把 [reply]/[@QQ] 指令转成真正的消息段（无指令时等价纯文本）
            outbound = build_outbound_builder(ctx.event, config).build(response_text)
            if outbound is None:
                # 只有指令、没有正文：不发空消息
                return
            ctx.response_messages = [outbound]

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
            if lock_acquired and session_lock is not None:
                if session_lock.locked():
                    session_lock.release()
            self.runtime.config.clear_session()
            self.pool.finish(job)

    async def _run_stream(self, ctx: LlmContext) -> None:
        """流式路径：按句子发送，并触发 pre_send / post_send / post_stream 钩子。"""
        from app.llm.chat import stream_response

        config = self.runtime.config
        pool_enabled = config.get("stream_send_pool_enabled", False)
        # 输出侧动作通道：只有本轮首片能带 [reply]/[@QQ]，后续片仅剥离标记
        outbound = build_outbound_builder(ctx.event, config)

        if not pool_enabled:
            sent_parts: list[str] = []
            interrupted = False

            async for item in stream_response(self.runtime, ctx.event, ctx):
                if self._should_break_stream(ctx):
                    interrupted = True
                    break

                msg = self._as_message(item, outbound)
                if msg is None:
                    continue

                if not await self._run_stage("pre_send", ctx, msg):
                    return
                if getattr(msg, "skip", False):
                    continue

                await self._send(ctx, msg)
                sent_parts.append(msg.text)

                await self._run_stage("post_send", ctx, msg)

            if interrupted:
                await self._save_sent_history(ctx, sent_parts)
                ctx.state["interrupted"] = True

            await self._run_stage("post_stream", ctx)
            return

        from app.llm.send_pool import StreamSendPool

        sent_parts = []
        interrupted = False

        async def send_message(msg: Message) -> None:
            await self._send(ctx, msg)
            sent_parts.append(msg.text)

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
            should_cancel=lambda: self._should_break_stream(ctx),
        )
        try:
            async for item in stream_response(self.runtime, ctx.event, ctx):
                if self._should_break_stream(ctx):
                    interrupted = True
                    break
                msg = self._as_message(item, outbound)
                if msg is not None:
                    await pool.put(msg)

            if not interrupted:
                await pool.finish()
                await pool.wait_drained()
        finally:
            await pool.shutdown()

        if interrupted:
            await self._save_sent_history(ctx, sent_parts)
            ctx.state["interrupted"] = True

        await self._run_stage("post_stream", ctx)

    def _as_message(self, item: Any, outbound) -> Message | None:
        """把流式产出物统一成待发送的 ``Message``。

        两条流式语义并存：

        - **文本即发言**（旧）：``stream_response`` 逐句 yield 字符串，由 ``outbound``
          解析首片的 ``[reply]``/``[@QQ]`` 指令；
        - **发言通道**（``speech_via_tool``）：``stream_response`` yield 的已经是构造好的
          ``Message``（气泡自带引用/@ 段），不能再过一遍指令解析——
          否则正文里的 ``[...]`` 会二次剥离。
        """
        if isinstance(item, Message):
            return item if item.segments else None
        return outbound.build(str(item or ""))

    def _should_break_stream(self, ctx: LlmContext) -> bool:
        """流式发送中途是否应当停止继续发剩余句子。

        两个条件同时成立才算"打断"：

        1. 本任务已被同会话新消息取代（``is_stale``）——代际只增不减；
        2. 该会话确实开启了回复打断（``interrupt_enable``）。

        第 2 条是尊重功能开关的原始契约（"收到新消息时取消剩余发送并开始新一次回复"）：
        开关关闭时，在跑的一轮仍按老行为把已生成的句子发完。
        """
        if ctx.job is None or not self.is_stale(ctx.job):
            return False
        try:
            return bool(self.runtime.config.get("interrupt_enable", False))
        except Exception:  # noqa: BLE001 —— 配置读取失败按"不打断"处理，保守优先
            return False

    async def _save_sent_history(self, ctx: LlmContext, sent_parts: list[str]) -> None:
        """回复被打断时，把实际已发送的内容写入历史。"""
        if not getattr(self.runtime, "interrupt_save_sent", True):
            return
        text = "\n".join(sent_parts).strip()
        if not text:
            return
        session_mgr = getattr(self.runtime, "session_mgr", None)
        if session_mgr is None:
            return
        session = session_mgr.get_session(ctx.session_id)
        if session is None:
            return
        cfg = getattr(self.runtime, "config", None)
        enabled = True
        if cfg is not None:
            try:
                enabled = bool(cfg.get("clean_output_parentheses", True))
            except Exception:
                enabled = True
        from app.llm.tags import maybe_strip_parentheses

        session_mgr.add_message(ctx.session_id, "assistant", maybe_strip_parentheses(text, enabled))
        await asyncio.to_thread(session_mgr.history.save_session, session)

    async def _run_stage(self, stage: str, ctx: LlmContext, msg: Any = None) -> bool:
        """执行某个 LLM 阶段的所有钩子。返回 False 表示应中止。

        实现抽到 ``app.llm.hooks.run_stage``：主动性/定时路径没有 LlmPipeline，
        需要同一套钩子语义（含 post_assemble）。
        """
        return await run_stage(self.runtime, stage, ctx, msg)

    async def _send(self, ctx: LlmContext, msg: Message) -> None:
        if ctx.event.message_type == "private":
            response = await ctx.bot.send_private_msg(ctx.event.user_id, msg)
        else:
            response = await ctx.bot.send_group_msg(ctx.event.group.group_id, msg)
        self._mark_sent_message_id(ctx, response)

        pm = getattr(self.runtime, "proactive", None)
        if pm is not None:
            try:
                await pm.on_bot_sent(ctx.session_id, ctx.event.message_type != "private")
            except Exception as e:
                # 主动消息状态更新失败不影响本条消息发送，但会导致沉默计时不准，需留痕
                from app.core.logger import logger

                logger.add_info(f"#{self.runtime.bot_id}").debug(
                    f"[LLM Pipeline] 更新主动消息发送状态失败（已忽略）: {e}"
                )

    def _mark_sent_message_id(self, ctx: LlmContext, response: Any) -> None:
        """把发送响应里的 message_id 补到刚写入的 assistant 历史条目上。

        有了句柄，机器人的发言才能被引用、才能在群聊环境记录里按 message_id 去重
        （否则同一句话会在"会话历史"和"环境记录"里各出现一份）。
        发送失败或响应缺 id 时静默跳过——这是增强信息，不是主流程。
        """
        data = response.get("data") if isinstance(response, dict) else None
        message_id = (data or {}).get("message_id") if isinstance(data, dict) else None
        if not message_id:
            return
        session_mgr = getattr(self.runtime, "session_mgr", None)
        if session_mgr is None or not hasattr(session_mgr, "mark_last_assistant_message_id"):
            return
        try:
            session_mgr.mark_last_assistant_message_id(ctx.session_id, message_id)
        except Exception as e:  # noqa: BLE001 - 句柄回填失败不影响发送结果
            from app.core.logger import logger

            logger.add_info(f"#{self.runtime.bot_id}").debug(
                f"[LLM Pipeline] 回填 message_id 失败（已忽略）: {e}"
            )

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
