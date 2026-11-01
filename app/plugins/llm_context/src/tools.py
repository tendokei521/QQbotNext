"""上下文解析的唯一工具：`get_context(target)`（重写版）。

target：`message:<id>` / `recent:<n>` / `forward:<id>` / `user:<qq>` / `image:<id>`。
渲染优先用 `runtime.message_assembly`（llm_prompt 提供），未接管时退回纯文本。
"""

from __future__ import annotations

from typing import Any

from app.agent.tools.spec import ToolContext, ToolSpec


def _renderer(runtime: Any):
    return getattr(runtime, "message_assembly", None)


def _plain_segments(message: Any) -> str:
    parts: list[str] = []
    for seg in message or []:
        if isinstance(seg, dict):
            stype = seg.get("type", "")
            data = seg.get("data") or {}
        else:
            stype = getattr(seg, "type", "")
            data = getattr(seg, "data", None) or {}
        if stype == "text":
            parts.append(str(data.get("text", "")))
        elif stype == "at":
            parts.append(f"@{data.get('qq', '')}")
        elif stype in ("image", "forward", "record", "video", "file"):
            parts.append(f"[{stype}:{data.get('file') or data.get('id') or ''}]")
        elif stype:
            parts.append(f"[{stype}]")
    return "".join(parts)


def _segments_to_text(runtime: Any, message: Any) -> str:
    renderer = _renderer(runtime)
    render = getattr(renderer, "render_segments", None) if renderer is not None else None
    if callable(render):
        try:
            return render(message)
        except Exception:  # noqa: BLE001
            pass
    return _plain_segments(message)


def _sender(message: Any) -> tuple[str, str]:
    for seg in message or []:
        if isinstance(seg, dict) and seg.get("type") == "at":
            data = seg.get("data") or {}
            return str(data.get("qq", "")), str(data.get("name", ""))
    return "", ""


async def _handle(tool_ctx: ToolContext, args: dict) -> str:
    runtime = tool_ctx.runtime
    bot = tool_ctx.bot
    target = str(args.get("target", "") or "").strip()
    kind, _, ref = target.partition(":")
    kind = kind.strip().lower()
    ref = ref.strip()
    if not kind or not ref:
        return "用法：get_context(target=\"message:<id> | recent:<n> | forward:<id> | user:<qq> | image:<id>\")"

    if kind == "message":
        if bot is None:
            return "当前没有可用连接，取不到消息。"
        try:
            resp = await bot.get_msg(ref)
        except Exception as e:  # noqa: BLE001
            return f"取回消息 {ref} 失败：{e}"
        data = (resp or {}).get("data", {}) or {}
        if not data:
            return f"没有找到消息 {ref}（可能已过期或不可见）。"
        sender = data.get("sender", {}) or {}
        name = sender.get("card") or sender.get("nickname") or ""
        body = _segments_to_text(runtime, data.get("message"))
        return f"[msg:id={ref}][sender:id={sender.get('user_id', '')},name=\"{name}\"]{body}"

    if kind == "recent":
        try:
            limit = max(1, min(50, int(ref or 10)))
        except ValueError:
            limit = 10
        from app.agent.context.group import format_history_for_llm
        from app.agent.session.manager import SessionManager

        mgr = SessionManager(str(getattr(runtime, "bot_id", "")))
        history = mgr.get_history(tool_ctx.session_id, limit=limit)
        if not history:
            return "本地没有可读的会话记录。"
        is_private = str(tool_ctx.session_id).startswith("private_")
        rendered = format_history_for_llm(history, is_private=is_private)
        return "\n".join(str(item.get("content", "")) for item in rendered)

    if kind == "forward":
        if bot is None:
            return "当前没有可用连接，取不到合并转发。"
        try:
            resp = await bot.get_forward_msg(id=ref)
        except Exception as e:  # noqa: BLE001
            return f"取回合并转发 {ref} 失败：{e}"
        nodes = _normalize_forward(resp)
        if not nodes:
            return f"合并转发 {ref} 没有内容（可能已过期）。"
        lines: list[str] = []
        for index, node in enumerate(nodes[:30], start=1):
            body = _segments_to_text(runtime, node["message"])
            lines.append(
                f"[forward_message:id={ref}][node:{index}]"
                f"[sender:id={node['user_id']},name=\"{node['name']}\"]{body}"
            )
        if len(nodes) > 30:
            lines.append(f"[forward_more:count={len(nodes) - 30}]")
        return "\n".join(lines)

    if kind == "user":
        if bot is None:
            return "当前没有可用连接，查不到成员信息。"
        group_id = tool_ctx.group_id
        if group_id in (None, ""):
            event = getattr(tool_ctx, "event", None)
            group_id = getattr(getattr(event, "group", None), "group_id", None)
        try:
            resp = await bot.get_group_member_info(group_id=group_id, user_id=ref, no_cache=False)
        except Exception as e:  # noqa: BLE001
            return f"查询成员 {ref} 失败：{e}"
        data = (resp or {}).get("data", {}) or {}
        name = data.get("card") or data.get("nickname") or ""
        return f"[user:id={ref},name=\"{name}\",role={data.get('role', '')}]"

    if kind == "image":
        if bot is None:
            return "当前没有可用连接，取不到图片。"
        try:
            resp = await bot.get_msg(ref)
        except Exception as e:  # noqa: BLE001
            return f"取回图片消息 {ref} 失败：{e}"
        data = (resp or {}).get("data", {}) or {}
        urls: list[str] = []
        for seg in data.get("message", []) or []:
            stype = seg.get("type") if isinstance(seg, dict) else getattr(seg, "type", "")
            sdata = (seg.get("data") if isinstance(seg, dict) else getattr(seg, "data", {})) or {}
            if stype == "image":
                urls.append(str(sdata.get("url") or sdata.get("file") or ""))
        if not urls:
            return f"[image:id={ref}]（没有取到图片地址）"
        return f"[image:id={ref}]（图片内容需视觉模型查看；地址：{'; '.join(u for u in urls if u)}）"

    return f"不支持的 target 类型：{kind}（可用：message/recent/forward/user/image）"


def _normalize_forward(response: Any) -> list[dict]:
    payload = response.get("data") if isinstance(response, dict) else response
    if isinstance(payload, dict):
        nodes = payload.get("messages") or payload.get("content") or []
    elif isinstance(payload, list):
        nodes = payload
    else:
        nodes = []
    out: list[dict] = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        body = node.get("data") if isinstance(node.get("data"), dict) else node
        sender = body.get("sender") or {}
        out.append(
            {
                "user_id": sender.get("user_id") or body.get("user_id", ""),
                "name": sender.get("card") or sender.get("nickname") or body.get("nickname", ""),
                "message": body.get("message") or body.get("content") or [],
            }
        )
    return out


def build_context_tool() -> ToolSpec:
    return ToolSpec(
        name="get_context",
        description=(
            "取回上下文内容。target 形如 message:<id>（某条消息）、recent:<n>（最近 n 条）、"
            "forward:<id>（合并转发）、user:<qq>（群成员）、image:<id>（图片消息）。"
            "用户提到“那个/刚才/上一条”，或需要对某个 id 的内容做判断时调用；查不到就说明查不到。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "message:<id> / recent:<n> / forward:<id> / user:<qq> / image:<id>",
                }
            },
            "required": ["target"],
        },
        handler=_handle,
        permission="member",
        scopes=("*",),
        source="llm_context",
        category="上下文",
    )


async def collect_context_tools(scope: Any) -> list[ToolSpec]:
    config = getattr(scope, "config", None)
    enabled = True
    getter = getattr(config, "get", None)
    if callable(getter):
        try:
            enabled = bool(getter("context_enable", True))
        except Exception:  # noqa: BLE001
            enabled = True
    return [build_context_tool()] if enabled else []
