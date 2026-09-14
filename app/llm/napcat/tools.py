"""NapCat ToolSpec 构建与执行。"""

from __future__ import annotations

import json
import time
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


def resolve_action(tool: dict) -> str:
    """工具名 → 真实 OneBot action。

    默认与工具名相同；仅当工具名不能直接当 action 用时（例如 NapCat 的
    ``.ocr_image`` / ``.handle_quick_operation`` 这类带前导点的 Go-CQHTTP
    兼容接口，点名不合法无法作为 OpenAI function.name），条目才用
    ``action`` 字段显式声明真实 action。
    """
    return str(tool.get("action") or tool.get("name") or "")


# ---------- 戳一戳节流 ----------
# 主动性提示会鼓励模型在合适时机戳一戳；没有节流就会变成每条消息都戳。
POKE_ACTIONS = frozenset({"send_poke", "group_poke", "friend_poke"})
DEFAULT_POKE_COOLDOWN_SECONDS = 20
_POKE_LAST: dict[str, float] = {}
_POKE_LAST_MAX = 500


def _now() -> float:
    return time.monotonic()


def _poke_key(runtime, ctx: ToolContext | None, args: dict) -> str:
    """节流键：同一会话里对同一个人的戳一戳才互相计时。"""
    bot_id = str(getattr(runtime, "bot_id", "") or "")
    session_id = str(getattr(ctx, "session_id", "") or "") if ctx is not None else ""
    target = str((args or {}).get("user_id") or (args or {}).get("target_id") or "")
    return f"{bot_id}:{session_id}:{target}"


def _poke_cooldown(runtime) -> float:
    try:
        value = getattr(runtime, "config", None).get(
            "poke_cooldown_seconds", DEFAULT_POKE_COOLDOWN_SECONDS
        )
        return max(0.0, float(value))
    except Exception:
        return float(DEFAULT_POKE_COOLDOWN_SECONDS)


def poke_cooldown_left(runtime, ctx: ToolContext | None, args: dict) -> float:
    """距下次可戳还剩多少秒；0 表示可以戳。"""
    window = _poke_cooldown(runtime)
    if window <= 0:
        return 0.0
    last = _POKE_LAST.get(_poke_key(runtime, ctx, args))
    if last is None:
        return 0.0
    left = window - (_now() - last)
    return left if left > 0 else 0.0


def record_poke(runtime, ctx: ToolContext | None, args: dict) -> None:
    """记录一次成功的戳一戳（失败不占用冷却，避免模型无法重试）。"""
    key = _poke_key(runtime, ctx, args)
    _POKE_LAST[key] = _now()
    if len(_POKE_LAST) > _POKE_LAST_MAX:
        cutoff = _now() - 3600
        for stale in [k for k, ts in _POKE_LAST.items() if ts < cutoff]:
            _POKE_LAST.pop(stale, None)
        if len(_POKE_LAST) > _POKE_LAST_MAX:
            _POKE_LAST.clear()


async def _handler(runtime, tool: dict, ctx: ToolContext | None, args: dict) -> str:
    bot = getattr(ctx, "bot", None) if ctx is not None else None
    if bot is None:
        return "error: 当前上下文无可用 Bot"
    name = str(tool.get("name", ""))
    action = resolve_action(tool)
    args = args or {}
    if action in POKE_ACTIONS:
        left = poke_cooldown_left(runtime, ctx, args)
        if left > 0:
            return (
                f"error: 戳一戳冷却中（还需 {int(left) + 1} 秒）"
                "——同一会话不要连续戳同一个人"
            )
    debug = False
    try:
        debug = bool(getattr(runtime, "config", None).get("napcat_tools_debug", False))
    except Exception:
        pass
    if debug:
        logger.add_info("NapCatTool").info(
            f"[NapCatDebug] 请求 {name} action={action} args={json.dumps(args, ensure_ascii=False)}"
        )
    try:
        response = await bot.call_api(action, args)
    except Exception as e:
        logger.add_info("NapCatTool").warning(f"[NapCat] {name} 执行异常: {e}")
        return f"error: {name} 执行异常: {e}"
    if action in POKE_ACTIONS and isinstance(response, dict) and response.get("status") == "ok":
        record_poke(runtime, ctx, args)
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
            source="napcat",
            category=str(tool.get("category", "NapCat")),
        )
        specs.append(spec)
    return specs
