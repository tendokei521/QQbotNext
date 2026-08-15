"""模块声明：LLM 请求防抖。

在 LLM 流水线的 pre_request 阶段注册钩子：
- 同一会话（群/私聊）短时间内连续消息只触发一次 LLM 请求；
- 可选的合并模式会把等待窗口内的多条消息合并为一条 user_text。
"""

from app.llm import LlmContext
from app.modules import BaseModule, llm_hook
from .config_schema import SCHEMA


class Module(BaseModule):
    name = "LLM防抖"
    sign = "LlmDebounce"
    description = "同一会话短时间内多条消息合并为一次 LLM 请求"
    permission = "everyone"
    subscribe = ()
    default_config = {
        "enable": True,
        "debounce_seconds": 1.5,
        "merge_messages": False,
        "merge_separator": "\n",
    }
    config_schema = SCHEMA

    @llm_hook("pre_request", event_type="*", order=0)
    async def debounce_pre_request(self, ctx: LlmContext):
        """LLM 请求前进入请求池：只放行防抖窗口内的最后一条消息。"""
        if not self.config.get("enable", True):
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
