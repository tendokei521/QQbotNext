"""参与度 / 触发规则的提示词段（L1，order 54）。

只放文案与段构造；门控与预算逻辑仍在 `app/agent/participation.py`。
"""

from __future__ import annotations

from typing import Any

from .system import (
    DEFAULT_PARTICIPATION_LEVEL as DEFAULT_LEVEL,
    LEGACY_TRIGGER_TEXT,
    LEVEL_PROMPT,
    SILENCE_RULE,
)


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
    """L1 段：把当前回复模式的内置规则告诉模型（随回复设置动态变化）。

    私聊与群聊是两套语义：群聊才有"参与度/沉默"，私聊是一对一、对方直接找你，
    文本即回复。私聊段**不读 reply_mode**——它在私聊不生效。
    """
    if bool(getattr(req, "is_private", False)):
        return [{
            "role": "system",
            "content": (
                "### 私聊\n"
                "你正在和对方一对一私聊（QQ 私信）。对方直接找你，通常应当回应，"
                "不需要判断参与度，也不用等被 @。\n"
                "你输出的正文会直接作为消息发出；想说什么就用自己的话直接说，"
                "可以简短，也可以反问，但不要用沉默代替回应。"
            ),
        }]

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
            "【群聊参与度】\n"
            "所有群消息都会进入你的上下文，由你决定是否发言（发言需要使用send_message工具）。\n"
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
