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
        "include_at": True,
        "include_quote": True,
        "fetch_at_nickname": True,
        "fetch_quote_content": True,
    }
    config_schema = SCHEMA

    # ==================== 用户信息感知 ====================

    @llm_hook("pre_request", event_type="message_group", order=-100)
    async def attach_user_context(self, ctx: LlmContext):
        """在 LLM 请求前把群聊上下文附加到 user_text。"""
        if not self.config.get("context_enable", True):
            return

        event = ctx.event
        config = self.config
        parts: list[str] = []

        # 1. 发送者信息
        if config.get("include_sender", True):
            nickname = event.user.card or event.user.nickname or ""
            if nickname:
                parts.append(f"发送者：{nickname}({event.user_id})")
            else:
                parts.append(f"发送者：{event.user_id}")

        # 2. @ 信息
        if config.get("include_at", True):
            at_list = await self._collect_at_info(ctx)
            if at_list:
                parts.append("@了：" + "、".join(at_list))

        # 3. 引用消息信息
        if config.get("include_quote", True):
            quote = await self._collect_quote_info(ctx)
            if quote:
                parts.append(
                    f"引用消息：{quote['text']}（发送者：{quote['sender_nickname']}({quote['sender_id']})）"
                )

        if parts:
            ctx.user_text = "\n".join(parts) + "\n" + ctx.user_text

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
            if qq == str(event.self_id):
                result.append("我")
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
