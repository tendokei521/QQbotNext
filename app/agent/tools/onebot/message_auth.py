"""OneBot 敏感工具的「消息凭证」鉴权（2026-11-03）。

## 规则

群聊里调用 `sensitivity in {high, critical}` 的 OneBot 工具时，模型必须额外传
``auth_message_id`` = **让你执行该操作的那条群消息**的 message_id。程序据此校验：

1. 消息真实存在、`get_msg` 能取到；
2. 消息属于**当前群**（防跨群引用别人的管理员消息）；
3. 消息发送者满足该敏感级要求的账号侧身份：
   - ``high``（群管理类）：bot 管理者或拥有者（``agent_manager_list`` / ``agent_owner_list``）；
   - ``critical``（自身账号类）：仅 bot 拥有者。

私聊场景不启用本鉴权（沿用触发事件的既有权限判定）。失败一律 fail-closed，
返回 ``error: PERMISSION_DENIED ...`` 给模型，并写一条审计日志。
"""

from __future__ import annotations

from typing import Any

from app.core.logger import logger

from .security import authorizer_roles

#: 模型必须回传的参数名（与作为"操作目标"的 message_id 区分开）
AUTH_ARG = "auth_message_id"
#: 参数说明（进 tool schema）
AUTH_ARG_DESCRIPTION = (
    "让你执行该操作的那条群消息的 message_id（必填）。消息 id 是 QQ 的 int32，"
    "可能为负数，原样传入即可。程序会用该消息的发送者身份"
    "校验权限：high 级工具需要 bot 管理者/拥有者，critical 级工具仅拥有者；"
    "消息不存在、不属于当前群或发送者权限不够都会被拒绝。不要编造 id。"
)


def parse_message_id(value: Any) -> int | None:
    """把模型/事件给的 message_id 解析成整数；不合法返回 ``None``。

    **负号必须放行**：QQ（NT / OneBot 实现）的 message_id 是 int32，可以是负数
    （线上实测 ``-1208685782``）。此前这里用 ``str.isdigit()``，负 id 会被判成
    "不是数字消息 id"，于是负 id 的消息永远无法通过凭证鉴权。
    只接受十进制整数串（可带 ``+``/``-`` 前缀）；空串、字母、全角数字均拒绝。
    """
    text = str(value or "").strip()
    if not text:
        return None
    body = text[1:] if text[:1] in ("+", "-") else text
    if not body.isascii() or not body.isdigit():
        return None
    try:
        return int(text)
    except ValueError:  # pragma: no cover —— 上面已保证是整数串
        return None


def _id_list(value: Any) -> set[str]:
    """名册值（list / 逗号 / 换行字符串）→ 字符串集合。"""
    if value is None:
        return set()
    if isinstance(value, str):
        items = value.replace("，", ",").replace(",", "\n").splitlines()
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        items = [value]
    return {str(item).strip() for item in items if str(item).strip()}


def _expected_group_id(runtime: Any, ctx: Any) -> str:
    """本轮所在群 id（事件优先；无事件轮次从会话 id / group_id 推导）。"""
    event = getattr(ctx, "event", None) if ctx is not None else None
    group = getattr(event, "group", None) if event is not None else None
    if group is not None:
        group_id = getattr(group, "group_id", "")
        if group_id not in (None, ""):
            return str(group_id)
    group_id = getattr(ctx, "group_id", None) if ctx is not None else None
    if group_id not in (None, ""):
        return str(group_id)
    session_id = str(getattr(ctx, "session_id", "") or "")
    if session_id.startswith("group_"):
        return session_id[len("group_"):]
    return ""


def _sender_of(data: dict) -> str:
    sender = data.get("sender") if isinstance(data, dict) else None
    if isinstance(sender, dict) and sender.get("user_id") not in (None, ""):
        return str(sender.get("user_id"))
    if isinstance(data, dict) and data.get("user_id") not in (None, ""):
        return str(data.get("user_id"))
    return ""


def _error(reason: str) -> str:
    return f"error: PERMISSION_DENIED {reason}"


async def authorize_from_message(
    runtime: Any,
    bot: Any,
    *,
    tool_name: str,
    args: dict,
    ctx: Any = None,
    sensitivity: str = "normal",
) -> str:
    """校验一次敏感工具调用的消息凭证；返回空串表示通过，否则返回回给模型的错误文本。"""
    roles = authorizer_roles(sensitivity)
    if not roles:  # normal 不该走到这里；按放行处理（调用方只在 high/critical 时挂校验器）
        return ""
    raw = str((args or {}).get(AUTH_ARG, "") or "").strip()
    if not raw:
        need = "bot 拥有者" if roles == ("owner",) else "bot 管理者或拥有者"
        return _error(
            f"缺少 {AUTH_ARG}：调用 {tool_name} 必须传入让你执行该操作的群消息 message_id"
            f"（该工具需要{need}授权）。"
        )
    mid = parse_message_id(raw)
    if mid is None:
        return _error(f"{AUTH_ARG} 必须是数字消息 id（可为负），收到 {raw!r}。")
    if bot is None or not callable(getattr(bot, "get_msg", None)):
        return _error("当前没有可用的 OneBot 连接，无法校验消息凭证。")

    expect_group = _expected_group_id(runtime, ctx)
    if not expect_group:
        return _error("当前不是群聊场景，无法按群消息校验权限。")

    try:
        response = await bot.get_msg(mid)
    except Exception as e:  # noqa: BLE001 —— 查不到 = 无法授权
        _audit(runtime, tool_name, raw, "",
               f"get_msg 失败: {e}")
        return _error(f"取不到消息 {raw}，无法校验权限（{e}）。")
    data = (response or {}).get("data") if isinstance(response, dict) else None
    if not isinstance(data, dict) or not data:
        _audit(runtime, tool_name, raw, "", "消息不存在或不可见")
        return _error(f"消息 {raw} 不存在或不可见，无法校验权限。")

    msg_group = data.get("group_id")
    if msg_group in (None, ""):
        _audit(runtime, tool_name, raw, "", "消息没有 group_id")
        return _error(f"消息 {raw} 不是群消息（或协议端未返回群号），无法校验权限。")
    if str(msg_group) != str(expect_group):
        _audit(runtime, tool_name, raw, "", f"跨群引用：msg_group={msg_group} expect={expect_group}")
        return _error(
            f"消息 {raw} 不属于当前群（消息群 {msg_group}，当前群 {expect_group}），"
            "不能用它授权本群操作。"
        )

    sender = _sender_of(data)
    if not sender:
        _audit(runtime, tool_name, raw, "", "取不到发送者")
        return _error(f"消息 {raw} 取不到发送者，无法校验权限。")

    config = getattr(runtime, "config", None)
    getter = getattr(config, "get", None)
    owners: set[str] = set()
    managers: set[str] = set()
    if callable(getter):
        try:
            owners = _id_list(getter("agent_owner_list", []))
            managers = _id_list(getter("agent_manager_list", []))
        except Exception:  # noqa: BLE001 —— 配置读取失败按无人有资格（fail-closed）
            owners, managers = set(), set()

    actual = "owner" if sender in owners else ("manager" if sender in managers else "member")
    ok = False
    if "owner" in roles and actual == "owner":
        ok = True
    elif "manager" in roles and actual in ("manager", "owner"):
        ok = True
    if not ok:
        need = "bot 拥有者" if roles == ("owner",) else "bot 管理者或拥有者"
        _audit(runtime, tool_name, raw, sender, f"权限不足：actual={actual} need={roles}")
        return _error(
            f"消息 {raw} 的发送者 {sender} 权限不足（需要{need}），"
            f"不能调用 {tool_name}。"
        )

    _audit(runtime, tool_name, raw, sender, f"通过（{actual}）")
    return ""


def _audit(runtime: Any, tool: str, message_id: str, sender: str, detail: str) -> None:
    """凭证鉴权审计：成功/失败都留痕（谁、哪条消息、结论）。"""
    try:
        bot_id = getattr(runtime, "bot_id", "?")
        logger.add_info(f"Auth:{bot_id}").info(
            f"[MessageAuth] tool={tool} auth_message_id={message_id} sender={sender or '-'} {detail}"
        )
    except Exception:  # noqa: BLE001 —— 审计失败不影响判定
        logger.debug("[MessageAuth] 审计日志写入失败（已忽略）")


__all__ = ["AUTH_ARG", "AUTH_ARG_DESCRIPTION", "authorize_from_message", "parse_message_id"]
