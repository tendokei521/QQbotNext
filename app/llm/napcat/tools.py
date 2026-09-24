"""NapCat ToolSpec 构建与执行。"""

from __future__ import annotations

import json
import time
from typing import Any

from app.llm import logger
from app.llm.group_log.store import store_of
from app.llm.napcat.manifest import NAP_CAT_TOOLS
from app.llm.napcat.security import resolve_tool_policy
from app.llm.tool import ToolContext, ToolSpec

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
    if action == EMOJI_LIKE_ACTION:
        params, error = _emoji_like_prepare(ctx, args)
        if error:
            return error
        if not params:
            # 语义解析成功但这条消息上已经有同一个表情 → 当作"已经满足"返回，不再下行
            return "这条消息上已经有同一个表情了，没有重复贴。"
        args = params
    debug = False
    try:
        debug = bool(getattr(runtime, "config", None).get("napcat_tools_debug", False))
    except Exception as e:
        logger.add_info("NapCatTool").debug(f"[NapCatTool] 读取 debug 开关失败，按关闭处理: {e}")
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
    if action == EMOJI_LIKE_ACTION and isinstance(response, dict) and response.get("status") == "ok":
        # 成功才记账：失败不写"我贴过"，否则模型会以为已经贴上了（幂等判断在这里失真）
        _emoji_like_record(runtime, ctx, args, str(args.get("emoji_id", "") or ""))
    if debug:
        logger.add_info("NapCatTool").info(
            f"[NapCatDebug] 响应 {name} response={json.dumps(response, ensure_ascii=False, default=str)}"
        )
    result = _format_result(response, name)
    max_len = 2000
    try:
        max_len = int(getattr(runtime, "config", None).get("napcat_tools_max_result", DEFAULT_MAX_RESULT) or DEFAULT_MAX_RESULT)
    except Exception as e:
        logger.add_info("NapCatTool").debug(f"[NapCatTool] 读取结果长度上限失败，使用默认 {max_len}: {e}")
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


# ---------- 表情回应：语义解析 + 贴前先读 ----------
# 贴错表情不可撤回（QQ 没有"取消回应"，只能换个表情盖过去），所以这里做三件事：
# 1. 语义 → emoji_id 交给词表解析，不让模型猜数字；解析不出来就报错而不是硬挑一个；
# 2. 贴之前先读这条消息上已有的回应：同一个表情已经贴过就跳过（幂等，省一次下行）；
# 3. 贴成功后写进群聊记录，模型下一轮能看到"我给这条贴了 X"。
EMOJI_LIKE_ACTION = "set_msg_emoji_like"


def _current_message_id(ctx: ToolContext | None, args: dict) -> tuple[str, str]:
    """解析要贴的目标消息：显式 message_id 优先，否则用本轮触发消息。

    返回 ``(message_id, error)``。模型在上下文里看不到消息 id（句柄按需渲染），
    所以"给当前这条贴"必须是零参数默认行为。
    """
    raw = str((args or {}).get("message_id", "") or "").strip()
    if raw:
        if not raw.isdigit():
            return "", f"error: message_id 必须是数字消息 id，收到 {raw!r}"
        return raw, ""
    extracted = (getattr(ctx, "extra", {}) or {}).get("trigger_message_id", "") if ctx is not None else ""
    if not extracted:
        event = getattr(ctx, "event", None)
        extracted = getattr(event, "message_id", "") if event is not None else ""
    text = str(extracted or "").strip()
    if not text:
        return "", (
            "error: 没有指定 message_id，且本轮没有可用的触发消息"
            "（主动/定时场景请显式传入 message_id）"
        )
    return text, ""


def _emoji_like_prepare(ctx: ToolContext | None, args: dict) -> tuple[dict, str]:
    """构造真正要发给 OneBot 的参数；返回 ``(params, error)``。

    ``params`` 为空且 ``error`` 为空 = "无需重复贴"，由调用方解释为已满足。
    """
    from app.llm import emoji_lexicon
    from app.llm.group_log.context import scope_for_tool_ctx

    args = args or {}
    message_id, error = _current_message_id(ctx, args)
    if error:
        return {}, error

    raw_emoji = str(args.get("emoji_id", "") or "").strip()
    tag = str(args.get("reaction", "") or "").strip()
    if not raw_emoji and not tag:
        return {}, (
            f"error: 需要给出 reaction（语义标签，如 {emoji_lexicon.available_tags()}）"
            "或 emoji_id（精确复用上下文里出现过的数字 id）"
        )

    runtime = getattr(ctx, "runtime", None) if ctx is not None else None
    store = store_of(runtime) if runtime is not None else None
    scope = scope_for_tool_ctx(ctx)
    observed = [r.get("emoji_id") for r in (store.reactions_of(scope, message_id) if store else [])]

    emoji_id, _source = emoji_lexicon.resolve(raw_emoji or tag, observed=observed)
    if not emoji_id:
        return {}, (
            f"error: 无法把 {raw_emoji or tag!r} 解析成表情 id。可用语义标签："
            f"{emoji_lexicon.available_tags()}；也可以直接用上下文里出现过的数字 emoji_id"
            "（记录里的 [♡66] 那种）。不要猜数字。"
        )

    # 贴前先读：同一个表情已经在这条消息上 → 不重复贴
    if store is not None and scope:
        for item in store.reactions_of(scope, message_id):
            if str(item.get("emoji_id")) == emoji_id:
                return {}, (
                    f"这条消息上已经有「{emoji_lexicon.label_for(emoji_id)}"
                    f"({emoji_id})」，无需重复；想表达别的意思请换一个。"
                )

    params = {
        "message_id": int(message_id) if message_id.isdigit() else message_id,
        "emoji_id": emoji_id,
    }
    return params, ""


def _emoji_like_record(runtime, ctx: ToolContext | None, args: dict, emoji_id: str) -> None:
    """贴成功后写进群聊记录（带 reason，便于回溯"当初为什么贴"）。"""
    store = store_of(runtime) if runtime is not None else None
    if store is None:
        return
    from app.llm.group_log.context import scope_for_tool_ctx
    from app.llm.group_log.events import KIND_EMOJI, LogEvent

    scope = scope_for_tool_ctx(ctx)
    if not scope:
        return
    message_id, _ = _current_message_id(ctx, args)
    if not message_id:
        return
    store.append_many([LogEvent(
        ts=int(time.time()),
        kind=KIND_EMOJI,
        scope=scope,
        group_id=str(getattr(ctx, "group_id", "") or "") if ctx is not None else "",
        message_id=message_id,
        user_id=str(getattr(runtime, "bot_id", "") or ""),
        nickname="我",
        payload={
            "emoji_id": emoji_id,
            "is_add": True,
            "reason": str((args or {}).get("reason", "") or ""),
        },
        by_me=True,
    )])


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
