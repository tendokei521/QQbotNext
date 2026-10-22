"""llm_context 模块的服务层：把搬进来的实现接到框架的基础接口上。

模块通过三个基础接口工作，**不接管任何东西**：

1. `context_providers` —— 每次请求贡献「指代块」；
2. `tool_sources` —— 提供 `expand_*` 系列工具；
3. `pre_request` 钩子 —— 每轮开始时让焦点表衰减（原本是框架 `tool_source` 里硬调的）。
"""

from __future__ import annotations

from typing import Any


async def referent_block(runtime: Any, session_id: str, user_text: str, ctx: Any = None) -> str:
    """指代消解块：「当前对话焦点 + 指代解析 + 已预取内容」。

    判据用**用户原始文本**（不含 ``发送者：``/``引用了：`` 之类的元信息前缀）——
    否则元信息里的 QQ 号会被误当成用户给出的消息 id（真实事故：
    ``get_msg(1901691195)`` 空转）。有事件时只认原始文本，**哪怕为空**：
    图片/语音这类没有文字的回合，回退到带 ``(QQ: …)`` 的 user_text 就会踩这个坑。

    这是从框架 `context_blocks._referent_block` 搬来的适配层（v3 归本模块）。
    """
    from app.domain.trigger import raw_user_text

    try:
        from app.plugins.llm_context.referent import build_block

        event = getattr(ctx, "event", None)
        if event is not None:
            return await build_block(runtime, session_id, raw_user_text(event), ctx)
        return await build_block(runtime, session_id, user_text, ctx)
    except Exception as e:  # noqa: BLE001 —— 指代是增强，失败不该毁掉整轮
        from app.core.logger import logger

        logger.add_info(f"#{getattr(runtime, 'bot_id', '?')}").debug(
            f"[Referent] 构建指代块失败（已忽略）: {e}"
        )
        return ""


class ReferentProvider:
    """上下文贡献者：产出「当前对话焦点 + 指代解析 + 已预取」块。"""

    def __init__(self, module: Any) -> None:
        self.module = module
        self.name = "referent"
        self.order = 40
        self.owner = module

    async def collect(self, scope: Any) -> str | None:
        text = await referent_block(
            getattr(scope, "runtime", None),
            scope.session_id,
            scope.user_text,
            SimpleCtx(scope),
        )
        return text or None


class SimpleCtx:
    """把 `TurnScope` 包成 `referent` 需要的 ctx 形状（只要 ``event`` / ``runtime``）。"""

    def __init__(self, scope: Any) -> None:
        self.event = getattr(scope, "event", None)
        self.runtime = getattr(scope, "runtime", None)
        self.session_id = getattr(scope, "session_id", "")


class ContextToolSource:
    """工具源：`expand_recent` / `expand_message` / `expand_user` 等按需展开工具。"""

    def __init__(self, module: Any) -> None:
        self.module = module
        self.name = "context_expand"
        self.order = 40
        self.owner = module

    async def collect(self, scope: Any) -> list:
        runtime = getattr(scope, "runtime", None)
        if runtime is None:
            return []
        from app.agent.policy import policy_get

        if not bool(policy_get(runtime, "context", "context_expand_enable", True)):
            return []
        from app.agent.tools.spec import ToolContext
        from app.plugins.llm_context.context_tools import build_context_tools

        # 优先复用本轮的 ToolContext（`extra["round_images"]` 等队列是共享的）；
        # 只有外部单独调用（没有请求上下文）时才自建一个。
        tool_ctx = getattr(scope, "tool_ctx", None) or ToolContext(
            module=self.module,
            bot=getattr(runtime, "bot", None),
            session_id=getattr(scope, "session_id", ""),
            event=getattr(scope, "event", None),
            runtime=runtime,
            user_id=getattr(scope, "user_id", None),
            group_id=getattr(scope, "group_id", None),
        )
        return list(build_context_tools(runtime, tool_ctx) or [])


def begin_turn(scope: Any) -> None:
    """每轮开始：让焦点表按轮次衰减（原框架 `tool_source` 里的硬调用）。"""
    from app.plugins.llm_context import focus

    runtime = getattr(scope, "runtime", None)
    focus.begin_turn(getattr(runtime, "bot_id", ""), scope.session_id)
