"""NapCat ToolSpec 构建与执行。"""

from __future__ import annotations

import json
from typing import Any

from app.llm import logger
from app.llm.tool import ToolContext, ToolSpec
from app.llm.napcat.manifest import NAP_CAT_TOOLS
from app.llm.napcat.security import resolve_tool_policy

DEFAULT_MAX_RESULT = 2000


def _format_result(response: dict | None, name: str) -> str:
    if response is None:
        return f"error: {name} 调用失败（未连接或超时）"
    status = response.get("status")
    if status != "ok":
        return f"error: {name} 调用失败: {response.get('retcode')} {response.get('message')}"
    data = response.get("data")
    try:
        text = json.dumps(data, ensure_ascii=False)
    except Exception:
        text = str(data)
    return text


async def _handler(runtime, tool: dict, ctx: ToolContext | None, args: dict) -> str:
    bot = getattr(ctx, "bot", None) if ctx is not None else None
    if bot is None:
        return "error: 当前上下文无可用 Bot"
    name = str(tool.get("name", ""))
    debug = False
    try:
        debug = bool(getattr(runtime, "config", None).get("napcat_tools_debug", False))
    except Exception:
        pass
    if debug:
        logger.add_info("NapCatTool").info(
            f"[NapCatDebug] 请求 {name} args={json.dumps(args, ensure_ascii=False)}"
        )
    try:
        response = await bot.call_api(name, args)
    except Exception as e:
        logger.add_info("NapCatTool").warning(f"[NapCat] {name} 执行异常: {e}")
        return f"error: {name} 执行异常: {e}"
    if debug:
        logger.add_info("NapCatTool").info(
            f"[NapCatDebug] 响应 {name} response={json.dumps(response, ensure_ascii=False, default=str)}"
        )
    result = _format_result(response, name)
    max_len = 2000
    try:
        max_len = int(getattr(runtime, "config", None).get("napcat_tools_max_result", DEFAULT_MAX_RESULT) or DEFAULT_MAX_RESULT)
    except Exception:
        pass
    if len(result) > max_len:
        result = result[:max_len] + "\n…(结果过长已截断)"
    return result


def _ctx_scope(ctx: ToolContext | None) -> str | None:
    if ctx is None:
        return None
    event = getattr(ctx, "event", None)
    if event is None:
        return None
    event_type = getattr(event, "event_type", "") or ""
    if event_type == "message_group" or getattr(event, "group", None) is not None:
        return "group"
    if event_type == "message_private" or getattr(event, "user_id", None):
        return "private"
    return None


def build_napcat_tools(runtime: Any, ctx: ToolContext | None = None) -> list[ToolSpec]:
    """根据配置与当前会话作用域生成本轮可用的 NapCat 工具。"""
    specs: list[ToolSpec] = []
    scope = _ctx_scope(ctx)
    for tool in NAP_CAT_TOOLS:
        policy = resolve_tool_policy(runtime, tool)
        if not policy["enabled"] or policy["blocked"]:
            continue
        scopes = policy["scopes"]
        # 会话过滤：群聊只保留 group/*，私聊只保留 private/*
        if scope is not None and "*" not in scopes and scope not in scopes:
            continue

        name = str(tool.get("name", ""))
        description = str(tool.get("description", ""))
        parameters = tool.get("parameters") or {"type": "object", "properties": {}}
        permission = policy["permission"]

        async def _tool_handler(ctx: ToolContext, args: dict, _tool=tool):
            return await _handler(runtime, _tool, ctx, args)

        spec = ToolSpec(
            name=name,
            description=description,
            parameters=parameters,
            handler=_tool_handler,
            permission=permission,
            scopes=scopes,
            module=None,
        )
        specs.append(spec)
    return specs
