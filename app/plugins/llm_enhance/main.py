"""模块声明：LLM 增强。

框架级上下文注入（用户信息感知 / 私信 QQ / 回复打断）已迁移到 `app/llm/enhance.py`。
本模块保留「调试 Prompt」配置。
"""

from app.agent import LlmContext, logger
from app.core.logger import account_label
from app.plugin import BaseModule, llm_hook

from .config_schema import SCHEMA


class Module(BaseModule):
    name = "LLM增强"
    sign = "LlmEnhance"
    description = "连发的消息自动合并，补齐用户信息再回复"
    permission = "everyone"
    subscribe = ()
    category = "LLM"
    pinned = True
    default_config = {
        "debug_prompt": False,
    }
    config_schema = SCHEMA

    @llm_hook("pre_request", event_type="*", order=30)
    async def debug_prompt_hook(self, ctx: LlmContext):
        """开启调试时，标记本轮需要打印完整 prompt。"""
        enabled = bool(self.config.get("debug_prompt", False))
        ctx.state["debug_prompt"] = enabled
        if enabled:
            logger.add_info(account_label(self.bot_id)).info(
                f"[Prompt] {ctx.session_id} user_text:\n{ctx.user_text}"
            )
