"""这一轮要交给模型的工具有哪些、从哪儿来。

## 本模块只负责

`_collect_llm_ext`（模块工具 + 技能 + 知识库 + MCP + 内置系统工具 + 定时工具）以及它
用到的两个开关判定（`_context_expand_flags` / `_images_enabled`）。

## 为什么单独成文件

工具来源每加一类（MCP、知识库、定时……）就往里塞一段，到 129 行已经难读。它只依赖
`ToolContext` 与各工具的 builder，与「这一轮怎么说话」无关。
"""

from __future__ import annotations

from app.agent import logger
from app.agent.tools.spec import ToolContext


def _images_enabled(config) -> bool:
    """图片是否随请求传给模型（默认开；仅对声明了 image 模态的模型生效）。"""
    try:
        return bool(config.get("image_understanding_enable", True))
    except Exception:  # noqa: BLE001
        return True


def _context_expand_flags(config) -> dict:
    """渲染层「骨架预展开」开关。

    - ``resolve_at``：复用既有的 ``fetch_at_nickname``（@ 昵称反查总开关）；
    - ``mark_unresolved``：``context_expand_enable``，控制 ``【未展开:...】`` 标记
      （与按需展开能力同开同关：标记必须可被解决，否则只会诱导模型空转）。
    """
    flags = {"resolve_at": True, "mark_unresolved": True}
    for key, name in (("fetch_at_nickname", "resolve_at"), ("context_expand_enable", "mark_unresolved")):
        try:
            flags[name] = bool(config.get(key, True))
        except Exception as e:
            # 配置读取异常不能影响回复主流程，退回默认（开启）并留痕
            logger.add_info("Chat").debug(f"[Context] 读取配置 {key} 失败，按默认开启处理: {e}")
    return flags


async def _collect_llm_ext(
    runtime,
    event,
    session_id: str,
    is_private: bool,
    schedule_enable: bool,
    *,
    bot=None,
    user_id=None,
    group_id=None,
):
    """收集本次请求的模块工具 + 技能 + 知识库 + MCP + ToolContext。

    返回 (specs, skill_blocks, ctx)；specs 已包含内置 schedule_task。

    ``event`` 允许为 None（主动消息 / 定时任务路径没有触发事件）。此时会话目标必须由调用方
    用 ``bot`` / ``user_id`` / ``group_id`` 显式给出——会话类工具（get_current_session /
    get_chat_history / expand_context）本来就是从 ToolContext 推导当前会话，不依赖事件。
    """
    if bot is None and event is not None:
        bot = getattr(event, "bot", None)
    if user_id is None and event is not None:
        user_id = getattr(event, "user_id", None)
    if group_id is None and event is not None:
        group = getattr(event, "group", None)
        group_id = getattr(group, "group_id", None) if group is not None else None

    specs = []
    if schedule_enable:
        # 工具在 tools/schedule.py，引擎在 proactivity/scheduler.py——分开是为了不造环
        from app.plugins.llm_schedule.schedule_tool import build_schedule_tool

        specs.append(build_schedule_tool(runtime, session_id, is_private))

    if getattr(runtime, "llm_tools", None) is not None:
        specs.extend(runtime.llm_tools.enabled_specs())

    skill_blocks = []
    if getattr(runtime, "skills", None) is not None:
        skill_blocks = runtime.skills.prompt_blocks()

    ctx = ToolContext(
        module=runtime,
        bot=bot,
        session_id=session_id,
        event=event,
        runtime=runtime,
        user_id=user_id,
        group_id=group_id,
    )

    # 系统级会话上下文工具（不展示在 OneBot 前端清单）
    from app.agent.tools.session_tools import build_session_tools

    specs.extend(build_session_tools(runtime, ctx))


    # Tavily 联网搜索（系统级工具，不进入 OneBot 前端清单）
    if bool(runtime.config.get("tavily_enable", False)):
        tavily_api_key = str(runtime.config.get("tavily_api_key", "") or "").strip()
        if tavily_api_key:
            from app.agent.tools.web_search import build_tavily_tool

            specs.append(build_tavily_tool(runtime))





    # 模块注册的工具源（OneBot / MCP / 记忆 / 知识库做成模块时的接入点）。
    # 放在过滤之前：它们产出的工具与框架工具走同一套四类开关过滤。
    if getattr(runtime, "tool_sources", None):
        from app.agent.loop.providers import TurnScope, collect_tool_specs

        scope = TurnScope(
            runtime=runtime,
            config=getattr(runtime, "config", None),
            session_id=session_id,
            is_private=is_private,
            user_id=str(user_id or ""),
            group_id=group_id,
            # 关键：复用本轮的 ToolContext（round_images 等队列是本轮共享的）
            tool_ctx=ctx,
            event=event,
        )
        specs.extend(await collect_tool_specs(runtime, scope))

    # 统一按四类工具的用户开关过滤：
    # - 系统工具由 system_tools_enabled + 前置功能开关共同决定
    # - 模块工具 / MCP 工具由各自 enabled map 单独控制
    # - OneBot 工具已在上方由 security.resolve_tool_policy 过滤，这里直接放行
    from app.agent.tools.policy import is_system_tool_enabled

    system_enabled = runtime.config.get("system_tools_enabled", {}) or {}
    module_enabled = runtime.config.get("module_tools_enabled", {}) or {}
    mcp_enabled = runtime.config.get("mcp_tools_enabled", {}) or {}
    filtered: list = []
    for spec in specs:
        if spec.source == "onebot":
            filtered.append(spec)
        elif spec.source == "mcp":
            if not (isinstance(mcp_enabled, dict) and not mcp_enabled.get(spec.name, True)):
                filtered.append(spec)
        elif spec.source == "module":
            if not (isinstance(module_enabled, dict) and not module_enabled.get(spec.name, True)):
                filtered.append(spec)
        elif spec.source == "system":
            if is_system_tool_enabled(runtime, spec):
                filtered.append(spec)
        else:
            filtered.append(spec)

    return filtered, skill_blocks, ctx
