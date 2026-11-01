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

    if kind in ("forward", "forward_full"):
        if bot is None:
            return "当前没有可用连接，取不到合并转发。"
        from . import forward_cache, summarize

        # 已缓存的摘要：直接给摘要（便宜），并提示可再取全文
        if kind == "forward":
            cached = forward_cache.get(getattr(runtime, "bot_id", ""), ref)
            if cached:
                forward_cache.touch(getattr(runtime, "bot_id", ""), ref)
                return (
                    f"[forward_message:id={ref}][forward_summary:nodes={cached.get('nodes', 0)}]"
                    f"{cached.get('summary', '')}\n"
                    f"[forward_full:id={ref}]（需要原文时用 forward_full:{ref} 再取）"
                )
        try:
            resp = await bot.get_forward_msg(id=ref)
        except Exception as e:  # noqa: BLE001
            return f"取回合并转发 {ref} 失败：{e}"
        nodes = _normalize_forward(resp)
        if not nodes:
            return f"合并转发 {ref} 没有内容（可能已过期）。"
        full = _render_forward_full(runtime, ref, nodes)
        # 首次展开：大转发给全文，同时异步生成摘要供后续轮次复用
        if kind == "forward":
            chars = len(full)
            summarize.maybe_schedule(runtime, ref, nodes, chars)
            if not summarize.is_small(chars, len(nodes)):
                full += f"\n[forward_summary:pending]（摘要生成中，之后可直接用摘要；原文可用 forward_full:{ref}）"
        return full

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
            # 兼容老占位符传的是图片 file hash（不是消息 id）：尝试 get_image
            got = await _try_get_image(bot, ref)
            if got:
                return got
            return f"取回图片消息 {ref} 失败：{e}"
        data = (resp or {}).get("data", {}) or {}
        urls: list[str] = []
        for seg in data.get("message", []) or []:
            stype = seg.get("type") if isinstance(seg, dict) else getattr(seg, "type", "")
            sdata = (seg.get("data") if isinstance(seg, dict) else getattr(seg, "data", {})) or {}
            if stype != "image":
                continue
            url = str(sdata.get("url") or "")
            if not url:
                # 段里只有 file（hash）时，调 get_image 换可访问地址
                file_ref = str(sdata.get("file") or "")
                url = await _image_url(bot, file_ref) if file_ref else ""
            if url:
                urls.append(url)
        if not urls:
            return f"[image:id={ref}]（没有取到图片地址）"
        return f"[image:id={ref}]（图片内容需视觉模型查看；地址：{'; '.join(u for u in urls if u)}）"

    return f"不支持的 target 类型：{kind}（可用：message/recent/forward/user/image）"


async def _image_url(bot: Any, file_ref: str) -> str:
    """按 file/hash 换图片地址：先 get_image，再 call_api 兜底。"""
    getter = getattr(bot, "get_image", None)
    if callable(getter):
        try:
            resp = await getter(file=file_ref)
            data = (resp or {}).get("data", {}) or {}
            url = str(data.get("url") or "")
            if url:
                return url
        except Exception:  # noqa: BLE001
            pass
    caller = getattr(bot, "call_api", None)
    if callable(caller):
        try:
            resp = await caller("get_image", {"file": file_ref})
            data = (resp or {}).get("data", {}) or {}
            return str(data.get("url") or "")
        except Exception:  # noqa: BLE001
            return ""
    return ""


async def _try_get_image(bot: Any, file_ref: str) -> str:
    """按 file/url 直接取图；取不到返回空串。"""
    url = await _image_url(bot, file_ref)
    if not url:
        return ""
    return f"[image:id={file_ref}]（图片内容需视觉模型查看；地址：{url}）"


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


#: 单次返回的全文字符上限（超过截断，提示用 forward_full 再取）
FULL_MAX_CHARS = 20000


def _render_forward_full(runtime: Any, ref: str, nodes: list[dict]) -> str:
    """转发全文：每条一行结构化标记；超过上限截断并注明。"""
    lines: list[str] = []
    for index, node in enumerate(nodes[:30], start=1):
        body = _segments_to_text(runtime, node["message"])
        lines.append(
            f"[forward_message:id={ref}][node:{index}]"
            f"[sender:id={node['user_id']},name=\"{node['name']}\"]{body}"
        )
    if len(nodes) > 30:
        lines.append(f"[forward_more:count={len(nodes) - 30}]")
    text = "\n".join(lines)
    if len(text) > FULL_MAX_CHARS:
        text = text[:FULL_MAX_CHARS] + f"\n[forward_truncated:nodes={len(nodes)}]（用 forward_full:{ref} 可再取）"
    return text


def build_context_tool() -> ToolSpec:
    return ToolSpec(
        name="get_context",
        description=(
            "取回上下文内容。target 形如 message:<id>（某条消息）、recent:<n>（最近 n 条）、"
            "forward:<id>（合并转发：首次给全文，之后给摘要）、forward_full:<id>（强制给全文）、"
            "user:<qq>（群成员）、image:<id>（图片消息）。"
            "用户提到“那个/刚才/上一条”，或需要对某个 id 的内容做判断时调用；查不到就说明查不到。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "message:<id> / recent:<n> / forward:<id> / forward_full:<id> / user:<qq> / image:<id>",
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
