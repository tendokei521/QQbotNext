"""协议能力真源：账号属于哪个协议、该协议能响应哪些 OneBot action。

## 为什么需要它

框架内部（插件、工具、Agent）长期按 OneBot v11 的动作名说话；官方 QQ 机器人
只覆盖其中一小部分，且 ID、消息形态都不同。把「协议 → 能响应哪些 action」
集中到本模块，其它层只问两个问题：

- ``supports_action(protocol, action)`` —— 这个协议能不能做这件事；
- ``capability_view(protocol)`` —— 给 WebUI 的一小组布尔/枚举，页面据此决定
  显示、禁用还是整块拦截（前端不写 ``if (protocol === 'qqbot')``）。

本模块是**纯领域**：不 import 任何 IO / infrastructure / agent 的东西，
因此可以在离线（未连接）时照常回答，WebUI 与工具状态机都能用。
"""

from __future__ import annotations

from typing import Any

#: 协议标识（持久化在 bots.protocol）
PROTOCOL_ONEBOT = "onebot"
PROTOCOL_QQBOT = "qqbot"

#: 全部已实现的协议
PROTOCOLS: tuple[str, ...] = (PROTOCOL_ONEBOT, PROTOCOL_QQBOT)

#: 协议展示名（日志 / WebUI）
PROTOCOL_LABELS: dict[str, str] = {
    PROTOCOL_ONEBOT: "OneBot v11",
    PROTOCOL_QQBOT: "官方 QQ 机器人",
}

#: OneBot v11 覆盖清单里的全部 action → 用通配表示（清单在 agent 层，领域层不 import）
ALL_ACTIONS = "*"

#: 官方 QQ 机器人第一版能响应的 OneBot action。
#:
#: 只收「官方 OpenAPI 真有对应能力」的动作；群列表是**模拟**（从事件学习），
#: 见 docs/QQ_OFFICIAL_BOT.md §3.7.5。群管理/成员列表等按内邀权限实测后再加。
QQBOT_ACTIONS: frozenset[str] = frozenset({
    "send_group_msg",
    "send_private_msg",
    "send_msg",
    "delete_msg",
    "get_login_info",
    "get_group_list",
    "get_group_info",
    "get_group_member_list",
    "set_group_ban",
    "set_group_kick",
    "get_status",
})

#: 协议 → 能响应的 action；``ALL_ACTIONS`` 表示不限制
ACTIONS_BY_PROTOCOL: dict[str, frozenset[str] | str] = {
    PROTOCOL_ONEBOT: ALL_ACTIONS,
    PROTOCOL_QQBOT: QQBOT_ACTIONS,
}


class CapabilityUnavailableError(ValueError):
    """当前账号类型不支持这个操作（协议能力缺失）。

    三层拦截里它是**最后一道**：正常路径会先被工具状态机 / API 能力检查拦住；
    走到这里说明有调用方绕过了前面两层，错误必须能直接展示给用户与模型。
    """

    def __init__(self, protocol: str, capability: str, message: str = "") -> None:
        self.protocol = str(protocol or "")
        self.capability = str(capability or "")
        self.message = str(message or "") or unsupported_message(self.protocol, self.capability)
        super().__init__(self.message)


def normalize_protocol(value: Any) -> str:
    """把任意输入归一成受支持的协议名（未知值按 OneBot，保证老配置可用）。"""
    text = str(value or "").strip().lower()
    return text if text in PROTOCOLS else PROTOCOL_ONEBOT


def protocol_label(protocol: Any) -> str:
    return PROTOCOL_LABELS.get(normalize_protocol(protocol), str(protocol or ""))


def actions_of(protocol: Any) -> frozenset[str] | str:
    """该协议能响应的 action 集合；``ALL_ACTIONS`` 表示不限制。"""
    return ACTIONS_BY_PROTOCOL.get(normalize_protocol(protocol), QQBOT_ACTIONS)


def supports_action(protocol: Any, action: Any) -> bool:
    """该协议能否响应某个 OneBot action。空 action 按「不支持」处理。"""
    name = str(action or "").strip()
    if not name:
        return False
    actions = actions_of(protocol)
    if actions == ALL_ACTIONS:
        return True
    return name in actions


def unsupported_message(protocol: Any, action: Any) -> str:
    """给用户/模型看的一句话（不暴露内部实现）。"""
    label = protocol_label(protocol)
    name = str(action or "").strip() or "该操作"
    return f"{label}不支持该操作：{name}"


def capability_view(protocol: Any) -> dict[str, Any]:
    """给 WebUI 的能力摘要（小、稳定、可 JSON 化）。

    值可能是 bool 或枚举字符串（``group_list`` 用 ``native/learned/none``）。
    前端只读这个字段，不按协议名写分支。
    """
    proto = normalize_protocol(protocol)
    if proto == PROTOCOL_ONEBOT:
        return {
            "onebot_tools": True,
            "send_text": True,
            "send_image": True,
            "recall": True,
            "group_list": "native",
            "group_admin": True,
            "member_list": True,
            "proactive": True,
            "webhook": False,
        }
    return {
        "onebot_tools": False,
        "send_text": True,
        "send_image": True,    # 富媒体：本地分片上传 / 公网 URL 上传
        "recall": True,
        "group_list": "learned",
        "group_admin": True,   # 禁言/踢人：需机器人是群管理员，成员管理可能内邀
        "member_list": True,   # 官方接口存在，但多数账号需内邀白名单（11253）
        "proactive": False,    # 被动回复模型；主动消息按配置单独开
        "webhook": True,
    }


def capability_notes(protocol: Any) -> list[str]:
    """给 WebUI 的补充说明（能力受限的「为什么」）。"""
    proto = normalize_protocol(protocol)
    if proto == PROTOCOL_QQBOT:
        return [
            "默认只接收 @ 机器人的群消息；全量消息需群主在群设置里开启。",
            "群列表来自收到过的消息，可能不全；群名需账号有「群信息」接口权限（11253）。",
            "回复必须带 msg_id（被动窗口：群 5 分钟 / 单聊 60 分钟）。",
            "用户标识是 openid，不是 QQ 号；不同账号之间不可关联。",
            "群成员管理与禁言/踢人需要机器人是群管理员；成员列表多数账号为内邀（11253）。",
        ]
    return []
