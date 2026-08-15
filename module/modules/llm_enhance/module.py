"""模块声明：LLM 增强。

合并原 llm_debounce（请求防抖）和 llm_user_context（群聊用户信息感知）：
- 防抖：同一会话短时间内连续消息只触发一次 LLM 请求；
- 用户信息感知：群聊请求前附加发送者 / @ / 引用消息上下文。
"""

from app.llm import LlmContext
from app.modules import BaseModule, llm_hook
from .config_schema import SCHEMA


class Module(BaseModule):
    name = "LLM增强"
    sign = "LlmEnhance"
    description = "LLM 请求防抖 + 群聊用户信息感知"
    permission = "everyone"
    subscribe = ()
    default_config = {
        # 防抖
        "debounce_enable": True,
        "debounce_seconds": 1.5,
        "merge_messages": False,
        "merge_separator": "\n",
        # 用户信息感知
        "context_enable": True,
        "include_sender": True,
        "include_mentioned": True,
        "include_quote": True,
        "include_quote_sender": True,
        "include_sent": True,
        "fetch_at_nickname": True,
        "fetch_quote_content": True,
        # 调试
        "debug_prompt": False,
    }
    config_schema = SCHEMA

    # ==================== 用户信息感知 ====================

    @llm_hook("pre_request", event_type="*", order=-100)
    async def collect_user_context(self, ctx: LlmContext):
        """收集上下文信息，暂存到 ctx.state，不直接修改 user_text。"""
        if not self.config.get("context_enable", True):
            return

        event = ctx.event
        info = {
            "sender": None,
            "mentioned": [],
            "quote": None,
            "quote_sender": None,
        }

        if event.event_type == "message_group":
            # 发送者
            if self.config.get("include_sender", True):
                nickname = event.user.card or event.user.nickname or ""
                info["sender"] = f"{nickname}({event.user_id})" if nickname else str(event.user_id)

            # 提到了（自动过滤机器人自身）
            if self.config.get("include_mentioned", True):
                info["mentioned"] = await self._collect_at_info(ctx)

        # 引用消息
        if self.config.get("include_quote", True):
            quote = await self._collect_quote_info(ctx)
            if quote:
                info["quote"] = quote["text"]
                info["quote_sender"] = f"{quote['sender_nickname']}({quote['sender_id']})"

        ctx.state["user_context"] = info

    @llm_hook("pre_request", event_type="*", order=20)
    async def format_user_context(self, ctx: LlmContext):
        """在防抖/合并后，把上下文格式化为最终 user_text。"""
        info = ctx.state.get("user_context")
        if not info:
            return

        event = ctx.event
        is_group = event.event_type == "message_group"
        parts: list[str] = []

        if is_group:
            if self.config.get("include_sender", True) and info.get("sender"):
                parts.append(f"发送者：{info['sender']}")
            if self.config.get("include_mentioned", True) and info.get("mentioned"):
                parts.append("提到了：" + "、".join(info["mentioned"]))

        if self.config.get("include_quote", True) and info.get("quote"):
            if is_group and self.config.get("include_quote_sender", True) and info.get("quote_sender"):
                parts.append(f"引用了：{info['quote_sender']}发送的引用消息：“{info['quote']}”")
            else:
                parts.append(f"引用了：{info['quote']}")

        if self.config.get("include_sent", True) and ctx.user_text.strip():
            parts.append(f"发送了：{ctx.user_text.strip()}")

        if parts:
            ctx.user_text = "\n".join(parts)

    async def _collect_at_info(self, ctx: LlmContext) -> list[str]:
        event = ctx.event
        if not event.message:
            return []

        result: list[str] = []
        for seg in event.message:
            if seg.type != "at":
                continue
            qq = str(seg.data.get("qq", "") or "")
            if not qq:
                continue

            # 过滤机器人自身（self_id / bot_id 都过滤）
            if qq in (str(event.self_id), str(getattr(event, "bot_id", "") or "")):
                continue

            # 全体成员
            if qq in ("all", "0"):
                result.append("全体成员")
                continue

            nickname = qq
            if self.config.get("fetch_at_nickname", True):
                fetched = await self._fetch_group_member_nickname(ctx, qq)
                if fetched:
                    nickname = fetched
            result.append(f"{nickname}({qq})")
        return result

    async def _fetch_group_member_nickname(self, ctx: LlmContext, qq: str) -> str:
        event = ctx.event
        group_id = getattr(event.group, "group_id", None)
        if not group_id or not event.bot:
            return ""

        cache_key = f"llm_enhance:nick:{event.bot_id}:{group_id}:{qq}"
        cached = self.ctx.services.cache.get(cache_key)
        if cached:
            return cached

        try:
            resp = await event.bot.get_group_member_info(group_id=group_id, user_id=int(qq))
            data = (resp or {}).get("data", {}) or {}
            nickname = data.get("card") or data.get("nickname") or ""
            if nickname:
                self.ctx.services.cache.set(cache_key, nickname, 600)
                return nickname
        except Exception:
            pass
        return ""

    async def _collect_quote_info(self, ctx: LlmContext) -> dict | None:
        event = ctx.event
        if not event.bot or not self.config.get("fetch_quote_content", True):
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
        from app.domain.message import Message

        if isinstance(message, str):
            return message
        msg = Message.from_onebot(message)
        text = msg.text
        if text:
            return text
        if msg.segments:
            return "[" + ",".join(s.type for s in msg.segments) + "]"
        return ""

    # ==================== 防抖 ====================

    @llm_hook("pre_request", event_type="*", order=0)
    async def debounce_pre_request(self, ctx: LlmContext):
        """LLM 请求前进入请求池：只放行防抖窗口内的最后一条消息。"""
        if not self.config.get("debounce_enable", True):
            return

        pool = ctx.runtime.llm_pipeline.pool
        raw_debounce = self.config.get("debounce_seconds", 1.5)
        debounce = float(raw_debounce) if raw_debounce is not None else 1.5
        ok = await pool.wait_for_continue(ctx.job, debounce=debounce)
        if not ok:
            ctx.job.skip = True
            return

        if self.config.get("merge_messages", False):
            texts = pool.take_pending_texts(ctx.job.group_key)
            if texts:
                separator = str(self.config.get("merge_separator", "\n") or "\n")
                ctx.user_text = separator.join(texts)

    # ==================== 调试 ====================

    @llm_hook("pre_request", event_type="*", order=30)
    async def debug_prompt_hook(self, ctx: LlmContext):
        """开启调试时，标记本轮需要打印完整 prompt。"""
        enabled = bool(self.config.get("debug_prompt", False))
        ctx.state["debug_prompt"] = enabled
        if enabled:
            from app.llm import logger

            logger.add_info(f"#{self.bot_id}").info(
                f"[Prompt] {ctx.session_id} user_text:\n{ctx.user_text}"
            )
