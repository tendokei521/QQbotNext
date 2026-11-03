"""参与度 / 触发规则的提示词段（L1，order 54）。

只放文案与段构造；门控与预算逻辑仍在 `app/agent/participation.py`。
"""

from __future__ import annotations

from typing import Any

#: 参与度等级 → 提示词说明
LEVEL_PROMPT: dict[str, str] = {
    "very_high": (
        "你在群里的参与度是「超高」：几乎每条消息都可以接话。"
        "优先回应提问、观点、梗和需要帮助的内容；连续多条时挑最有价值的一条回应，不要逐条复读。"
    ),
    "high": (
        "你在群里的参与度是「高」：只参与值得讨论的话题（提问、观点、求助、梗、争论）。"
        "纯寒暄、纯表情、重复刷屏、与你无关的闲聊不要接。"
    ),
    "medium": (
        "你在群里的参与度是「中」：只参与你正在参与、或刚刚延续的话题。"
        "别人开了新话题且没有叫到你时保持沉默；同一话题连续对话时可以接着聊。"
    ),
    "low": (
        "你在群里的参与度是「低」：群里的消息都会给你看，但只有被提到时才接——"
        "被 @、被回复、被叫名字，或明显在说与你相关的事；没被提到的消息保持沉默。"
        "判断是否被提到、是否值得接，由你自己根据上下文决定。"
    ),
    "very_low": (
        "你在群里的参与度是「极低」：只接与自己相关的消息——被 @、被回复，"
        "或话题确实牵涉到你（在说你、问你、讨论你参与过的事）时才开口；"
        "其余任何情况都必须沉默。"
    ),
}

DEFAULT_LEVEL = "low"

SILENCE_RULE = (
    "\n\n【沉默】不需要回复时，不要输出任何正文，也不要调用发送工具——不发言就是正常回复。"
    "不要用“（沉默）”“不说话”之类的文字代替沉默。"
)

LEGACY_TRIGGER_TEXT: dict[str, str] = {
    "at": "只有被 @ 时系统才会请求你回复；没被 @ 的群消息你看不到，也不会被要求回复。",
    "keyword": "只有消息命中配置的关键词时系统才会请求你回复；其余群消息不会被要求回复。",
    "both": "只有被 @ 或消息命中关键词时系统才会请求你回复；其余群消息不会被要求回复。",
    "all": "每条群消息都可能请求你回复（参与概率 {chance}%）；被请求时请按人设自然地回应，不需要说明自己在“参与”。",
    "none": "当前群聊不会请求你回复；如被显式指令调用，正常回应即可。",
}


def _reply_config_getter(req: Any):
    """取"本轮生效的回复配置"读取器。

    优先用 pipeline 放在 ``ctx.state["reply_config"]`` 的**生效值**（账号级 + 群级覆盖 +
    派生触发方式）；没有时退回 `req.config`（账号级）。
    """
    ctx = getattr(req, "ctx", None)
    state = getattr(ctx, "state", None)
    effective = state.get("reply_config") if isinstance(state, dict) else None
    base = getattr(req, "config", None)
    base_get = getattr(base, "get", None)

    def get(key: str, default: Any = None) -> Any:
        if isinstance(effective, dict) and key in effective:
            return effective[key]
        if callable(base_get):
            return base_get(key, default)
        return default

    return get


def reply_rules_section(req: Any) -> list[dict]:
    """L1 段：把当前回复模式的内置规则告诉模型（随回复设置动态变化）。"""
    getter = _reply_config_getter(req)
    try:
        mode = str(getter("reply_mode", "legacy") or "legacy")
        group_enable = bool(getter("group_enable", False))
        private_enable = bool(getter("private_enable", True))
    except Exception:  # noqa: BLE001
        return []

    if mode == "participation":
        # 档位之间的差别**就写在这里**：本地只做成本门，由模型决定说不说。
        try:
            level = str(getter("participation_level", DEFAULT_LEVEL) or DEFAULT_LEVEL)
        except Exception:  # noqa: BLE001
            level = DEFAULT_LEVEL
        text = (
            "### 群聊参与度\n"
            "所有群消息都会进入你的上下文，由你决定是否发言（发言需要通过发送工具）。\n"
            + LEVEL_PROMPT.get(level, LEVEL_PROMPT[DEFAULT_LEVEL])
            + SILENCE_RULE
        )
        return [{"role": "system", "content": text}]

    # 旧版：条件触发。trigger_mode 走 `trigger_mode_from()` 折算旧键，
    # 否则只配了 trigger_at/trigger_keyword 的账号会被误显示成"仅 @"。
    try:
        from app.domain.dialogue_policy import trigger_mode_from

        raw = {
            "trigger_mode": getter("trigger_mode", ""),
            "trigger_at": getter("trigger_at", False),
            "trigger_keyword": getter("trigger_keyword", []) or [],
        }
        trigger = trigger_mode_from(raw)
        chance = int(getter("trigger_probability", 100) or 100)
        keywords = getter("trigger_keyword", []) or []
    except Exception:  # noqa: BLE001
        trigger, chance, keywords = "at", 100, []
    if trigger not in LEGACY_TRIGGER_TEXT:
        trigger = "at"
    detail = LEGACY_TRIGGER_TEXT[trigger].format(chance=chance)
    if trigger in ("keyword", "both") and keywords:
        detail += "关键词：" + "、".join(str(k) for k in keywords) + "。"
    scope = []
    if group_enable:
        scope.append("群聊")
    if private_enable:
        scope.append("私聊")
    text = (
        "### 回复触发规则\n"
        f"当前模式：条件触发（生效场景：{'、'.join(scope) or '无'}）。\n"
        + detail
    )
    return [{"role": "system", "content": text}]


def participation_section(req: Any) -> list[dict]:
    """兼容旧名：等同 reply_rules_section（唯一来源在 `reply_rules_section`）。"""
    return reply_rules_section(req)


__all__ = [
    "DEFAULT_LEVEL",
    "LEGACY_TRIGGER_TEXT",
    "LEVEL_PROMPT",
    "SILENCE_RULE",
    "participation_section",
    "reply_rules_section",
]
