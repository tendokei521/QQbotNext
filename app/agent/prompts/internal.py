"""后台内部 LLM 任务的提示词（不进入聊天上下文，只用于维护类调用）。"""

from __future__ import annotations

#: 历史压缩请求的主体说明（可被 ``context_compress_prompt`` 覆盖）
DEFAULT_COMPRESS_PROMPT = """请把上面的历史对话压缩成一份简洁但完整的摘要，用于后续对话无缝续接：
1. 覆盖所有核心话题及最终结论/结果；
2. 高亮最近的主要关注点；
3. 如有工具调用、任务进度或待办，说明当前状态和下一步；
4. 保留用户的重要个人信息、偏好、称呼等关键事实；
5. 使用与对话相同的语言输出。
只输出摘要内容，不要输出额外解释。"""

#: 压缩请求的 system 角色约束
COMPRESS_SYSTEM_PROMPT = "你是一个对话摘要助手。请只输出摘要内容。"

__all__ = ["COMPRESS_SYSTEM_PROMPT", "DEFAULT_COMPRESS_PROMPT"]
