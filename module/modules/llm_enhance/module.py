"""模块声明：LLM 增强。

包含：
- 防抖：同一会话短时间内连续消息只触发一次 LLM 请求；
- 用户信息感知：发送者 / 提到了 / 引用 / 发送内容 / 当前时间（开关已迁入 Agent 配置）；
- 回复打断：LLM 输出期间新消息中断（开关已迁入 Agent 配置）；
- 调试：开启后打印本轮 prompt。
"""

from app.llm import LlmContext
from app.modules import BaseModule, llm_hook
from .config_schema import SCHEMA


def _agent_cfg(ctx: LlmContext):
    """优先读 Agent 配置（用户信息感知/回复打断已迁入 Agent），无则返回 None。"""
    return getattr(getattr(ctx, "runtime", None), "config", None)


def _ctx_cfg(ctx: LlmContext, key: str, default):
    """从 Agent 配置读开关；异常/缺失时回退 default。"""
    cfg = _agent_cfg(ctx)
    if cfg is not None and hasattr(cfg, "get"):
        try:
            return cfg.get(key, default)
        except Exception:
            pass
    return default


def _ctx_enabled(ctx: LlmContext, key: str, default: bool) -> bool:
    return bool(_ctx_cfg(ctx, key, default))


class Module(BaseModule):
    name = "LLM增强"
    sign = "LlmEnhance"
    description = "LLM 请求防抖 + 用户信息感知 + 回复打断 + 调试"
    permission = "everyone"
    subscribe = ()
    category = "LLM"
    pinned = True
    default_config = {
        # 防抖
        "debounce_enable": True,
        "debounce_seconds": 1.5,
        "merge_messages": False,
        "merge_separator": "\n",
        # 调试
        "debug_prompt": False,
    }
    config_schema = SCHEMA

    # ==================== 用户信息感知 ====================

    @staticmethod
    def _raw_user_text(event) -> str:
        """从 OneBot message 字段（结构化段 JSON）中提取纯文本。"""
        parts: list[str] = []
        for seg in getattr(event, "message", []) or []:
            if isinstance(seg, dict):
                if seg.get("type") == "text":
                    data = seg.get("data", {}) or {}
                    parts.append(data.get("text", ""))
            else:
                if getattr(seg, "type", "") == "text":
                    data = getattr(seg, "data", {}) or {}
                    parts.append(data.get("text", ""))
        return "".join(parts).strip()

    @llm_hook("pre_request", event_type="*", order=-100)
    async def collect_user_context(self, ctx: LlmContext):
        """收集上下文信息，暂存到 ctx.state，不直接修改 user_text。

        开关来自 Agent 配置（用户信息感知已并入 Agent，取消总开关，按子项生效）。
        """
        event = ctx.event
        info = {
            "sender": None,
            "mentioned": [],
            "quote": None,
            "quote_sender": None,
            "sent_text": self._raw_user_text(event),
        }

        if event.event_type == "message_group":
            # 发送者
            if _ctx_enabled(ctx, "include_sender", True):
                nickname = event.user.card or event.user.nickname or ""
                info["sender"] = f"{nickname}({event.user_id})" if nickname else str(event.user_id)

            # 提到了（自动过滤机器人自身）
            if _ctx_enabled(ctx, "include_mentioned", True):
                info["mentioned"] = await self._collect_at_info(ctx)

        # 引用消息
        if _ctx_enabled(ctx, "include_quote", True):
            quote = await self._collect_quote_info(ctx)
            if quote:
                info["quote"] = quote["text"]
                info["quote_sender"] = f"{quote['sender_nickname']}({quote['sender_id']})"

        ctx.state["user_context"] = info

    @llm_hook("pre_request", event_type="*", order=20)
    async def format_user_context(self, ctx: LlmContext):
        """在防抖/合并后，把上下文格式化为最终 user_text，并在最新一轮开头插入当前时间。

        提示词格式由实验性细调配置控制：
        - meta_sender_style：legacy / new / single
        - meta_sent_style：legacy / new
        - meta_mask_nickname：是否把句子型昵称脱敏为 用户<QQ>
        """
        from datetime import datetime

        from app.llm.group_context import safe_sender_label

        info = ctx.state.get("user_context")
        if not info:
            return

        event = ctx.event
        is_group = event.event_type == "message_group"
        sender_style = str(_ctx_cfg(ctx, "meta_sender_style", "legacy") or "legacy").lower()
        sent_style = str(_ctx_cfg(ctx, "meta_sent_style", "legacy") or "legacy").lower()
        mask_nickname = _ctx_enabled(ctx, "meta_mask_nickname", False)

        def _render_sender(s: str) -> str:
            return safe_sender_label(s) if mask_nickname else s

        parts: list[str] = []
        sender_label = ""

        # 时间感知：放在最新一轮上下文的开头
        time_line = ""
        if _ctx_enabled(ctx, "include_time", True):
            time_line = f"(时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')})"

        if is_group:
            if _ctx_enabled(ctx, "include_sender", True) and info.get("sender"):
                sender = _render_sender(info["sender"])
                if sender_style == "single":
                    sender_label = sender
                elif sender_style == "new":
                    parts.append(f"发送者昵称：{sender}")
                else:
                    parts.append(f"发送者：{sender}")
            if _ctx_enabled(ctx, "include_mentioned", True) and info.get("mentioned"):
                mentioned = [_render_sender(m) for m in info["mentioned"]]
                parts.append("提到了(用户名)：" + "、".join(mentioned))

        if _ctx_enabled(ctx, "include_quote", True) and info.get("quote"):
            if is_group and _ctx_enabled(ctx, "include_quote_sender", True) and info.get("quote_sender"):
                quote_sender = _render_sender(info["quote_sender"])
                parts.append(f"引用了：{quote_sender}发送的引用消息：“{info['quote']}”")
            else:
                parts.append(f"引用了：{info['quote']}")

        sent_text = ctx.user_text.strip() or (info.get("sent_text") or "").strip()
        if _ctx_enabled(ctx, "include_sent", True) and sent_text:
            if sender_style == "single" and sender_label:
                parts.insert(0, f"{sender_label}: {sent_text}")
            elif sent_style == "new":
                parts.append(f"消息正文：{sent_text}")
            else:
                parts.append(f"发送了：{sent_text}")
        elif sender_style == "single" and sender_label:
            parts.insert(0, sender_label)

        if time_line:
            parts.insert(0, time_line)

        if parts:
            ctx.user_text = "\n".join(parts)
            # 标记已注入“发送者/发送了/引用/时间”等元信息，供 prompt 构建追加消歧说明
            ctx.state["message_meta_injected"] = True
        elif time_line:
            ctx.user_text = f"{time_line}\n{ctx.user_text}".strip()

    @llm_hook("pre_request", event_type="*", order=25)
    async def interrupt_config_hook(self, ctx: LlmContext):
        """把回复打断开关同步到运行时，供 LlmPipeline 判断是否打断旧任务。"""
        ctx.runtime.interrupt_enabled = _ctx_enabled(ctx, "interrupt_enable", False)
        ctx.runtime.interrupt_save_sent = _ctx_enabled(ctx, "interrupt_save_sent", True)
        if _ctx_enabled(ctx, "interrupt_debug", False):
            from app.llm import logger

            logger.add_info(f"#{self.bot_id}").info(
                f"[打断] {ctx.session_id} interrupt_enabled={ctx.runtime.interrupt_enabled}"
            )

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
            if _ctx_enabled(ctx, "fetch_at_nickname", True):
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
        if not event.bot or not _ctx_enabled(ctx, "fetch_quote_content", True):
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
