"""OneBot 消息段 → 内容流（重写版，唯一段级解析入口）。

- 文本原样（转义）
- at / 图片 / 表情 / 转发 / 语音 / 视频 / 文件 / 卡片 → 结构化标记或占位符
- reply 段不在这里出现（由渲染入口放到 ``[reply_to:…]``）
- 合并转发节点（get_forward_msg 的多实现返回）在这里归一
"""

from __future__ import annotations

from typing import Any

from .spec import PLACEHOLDERS, escape_body, marker, quote


def _data(seg: Any) -> dict:
    data = getattr(seg, "data", None)
    if isinstance(data, dict):
        return dict(data)
    return dict(data or {}) if data else {}


def _seg_id(data: dict) -> str:
    for key in ("file", "url", "id", "path", "fid"):
        value = data.get(key)
        if value:
            return str(value)
    return ""


def _seg(seg: Any) -> tuple[str, dict]:
    """兼容对象段（MessageSegment）与 OneBot 原始 dict 段。"""
    if isinstance(seg, dict):
        return str(seg.get("type", "") or ""), dict(seg.get("data") or {})
    return str(getattr(seg, "type", "") or ""), _data(seg)


def render_segments(message: Any) -> str:
    """渲染内容流（按原始段顺序，text/at/占位符交错）。"""
    parts: list[str] = []
    for seg in message or []:
        stype, data = _seg(seg)
        if stype == "text":
            parts.append(escape_body(data.get("text", "")))
        elif stype == "at":
            qq = str(data.get("qq", "") or "")
            if qq in ("all", "0"):
                parts.append(marker("at", "id=all", f"name={quote('全体成员')}"))
            elif qq:
                name = data.get("name") or data.get("card") or ""
                parts.append(marker("at", f"id={qq}", f"name={quote(name)}" if name else ""))
        elif stype == "reply":
            continue
        elif stype in PLACEHOLDERS:
            ident = _seg_id(data)
            parts.append(marker(PLACEHOLDERS[stype], f"id={ident}" if ident else ""))
        elif stype:
            parts.append(f"[{stype}]")
    return "".join(parts)


# ---------- 合并转发 ----------


def _node_content(node: dict) -> str:
    inner = node.get("content") or node.get("message") or []
    if isinstance(inner, str):
        return escape_body(inner)
    return render_segments(inner)


def _node_sender(node: dict) -> tuple[str, str]:
    sender = node.get("sender") or {}
    uid = sender.get("user_id") or node.get("user_id") or ""
    name = sender.get("card") or sender.get("nickname") or node.get("nickname") or ""
    return str(uid or ""), str(name or "")


def normalize_forward(response: Any) -> list[dict]:
    """``get_forward_msg`` 三种返回归一。

    - ``{"data": {"messages": [...]}}``（go-cqhttp / Lagrange）
    - ``{"data": {"content": [...]}}``（NapCat 部分版本）
    - 直接数组
    每项归一为 ``{"id": str, "user_id": str, "name": str, "content": str}``。
    """
    payload = response.get("data") if isinstance(response, dict) else response
    nodes = []
    if isinstance(payload, dict):
        nodes = payload.get("messages") or payload.get("content") or []
    elif isinstance(payload, list):
        nodes = payload
    result: list[dict] = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        body = node.get("data") if isinstance(node.get("data"), dict) else node
        uid, name = _node_sender(body)
        result.append(
            {
                "id": str(body.get("message_id") or body.get("id") or ""),
                "user_id": uid,
                "name": name,
                "content": _node_content(body),
            }
        )
    return result


def render_forward_nodes(nodes: list[dict], *, limit: int = 30) -> str:
    """转发内容渲染：每条一行结构化前缀 + 内容。"""
    lines: list[str] = []
    for node in nodes[:limit]:
        head = marker("sender", f"id={node.get('user_id', '')}", f"name={quote(node.get('name', ''))}")
        lines.append(f"{head}{node.get('content', '')}")
    if len(nodes) > limit:
        lines.append(f"[forward_more:count={len(nodes) - limit}]")
    return "\n".join(lines)
