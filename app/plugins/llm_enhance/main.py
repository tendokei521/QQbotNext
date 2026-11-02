"""模块声明：LLM 调试。

框架级上下文注入（用户信息感知 / 私信 QQ / 回复打断）已迁移到 `app/llm/enhance.py`。
本模块只保留「调试」能力：把本轮真正发给模型的**完整内容**、以及模型**返回的结果**写进日志。

历史：本模块原名「LLM增强」，除调试开关外的能力都已搬走，名字与功能不符，
2026-10-31 改名为「LLM调试」。
"""

from app.agent import LlmContext, logger
from app.core.logger import account_label
from app.plugin import BaseModule, llm_hook

from .config_schema import SCHEMA


class Module(BaseModule):
    name = "LLM调试"
    sign = "LlmEnhance"
    description = "开启后把本轮发给模型的内容、以及模型返回的结果写进日志"
    permission = "everyone"
    subscribe = ()
    category = "LLM"
    pinned = True
    default_config = {
        "debug_prompt": False,
        "debug_response": False,
    }
    config_schema = SCHEMA

    @llm_hook("pre_request", event_type="*", order=30)
    async def debug_prompt_hook(self, ctx: LlmContext):
        """开启调试时，标记本轮需要打印完整 prompt / 模型返回。

        真正打印在组装完成之后（`loop/turn_metrics.log_debug_prompt_if_enabled`）与
        一轮结束之后（`loop/turn_metrics.log_debug_response_if_enabled`），流式与非流式
        共用。这里只置开关、不自己输出内容，避免出现「只看到一行 user_text、
        看不到完整 prompt」这种半截日志。
        """
        prompt_enabled = bool(self.config.get("debug_prompt", False))
        response_enabled = bool(self.config.get("debug_response", False))
        ctx.state["debug_prompt"] = prompt_enabled
        ctx.state["debug_response"] = response_enabled
        if prompt_enabled:
            logger.add_info(account_label(self.bot_id)).debug("[Prompt] 本轮已开启完整提示词打印")
        if response_enabled:
            logger.add_info(account_label(self.bot_id)).debug("[Response] 本轮已开启模型返回打印")
