"""模块声明：LLM 增强。

当前包含：
- 防抖：同一会话短时间内连续消息只触发一次 LLM 请求；
- 调试：开启后打印本轮 prompt。

用户信息感知已上移到框架 LLM 流水线（app/llm/pipeline.py）。
"""

from app.llm import LlmContext
from app.modules import BaseModule, llm_hook
from .config_schema import SCHEMA


class Module(BaseModule):
    name = "LLM增强"
    sign = "LlmEnhance"
    description = "LLM 请求防抖 + 调试"
    permission = "everyone"
    subscribe = ()
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
