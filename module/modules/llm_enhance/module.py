"""模块声明：LLM 增强（兼容占位）。

框架级上下文注入（用户信息感知 / 私信 QQ / 时间 / 回复打断 / 调试）
已迁移到 `app/llm/enhance.py`，本模块保留占位避免旧配置/引用出错。
"""

from app.modules import BaseModule


class Module(BaseModule):
    name = "LLM增强"
    sign = "LlmEnhance"
    description = "LLM 请求防抖 + 用户信息感知 + 回复打断 + 调试（已迁移到框架层）"
    permission = "everyone"
    subscribe = ()
    category = "LLM"
    pinned = True
    default_config = {}
    config_schema = {}
