"""参与度驱动的回复模式（新版）：全量观察 + 本地门 + 参与度提示词。

设计要点：
- 群消息**先写入会话**（无论是否回复）；
- 用「参与度」做**本地规则门**，低等级消息根本不发请求（控制 QQ 场景成本）；
- 通过门的消息才进入 LLM，最终是否发言由系统提示词里的参与度约定 + 模型决定
  （沉默 = 不调用发送工具）；
- 预算保护：每账号 10 分钟窗口内最多 60 次请求 / 10M tokens。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Any

#: 参与度等级（从低到高）
LEVELS: tuple[str, ...] = ("very_low", "low", "medium", "high", "very_high")
DEFAULT_LEVEL = "low"

#: 预算：10 分钟窗口
BUDGET_WINDOW_SECONDS = 600
DEFAULT_MAX_REQUESTS = 60
DEFAULT_MAX_TOKENS = 10_000_000

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
        "你在群里的参与度是「低」：只有和你有关时才回复——被 @、被回复、被点名、"
        "或提到你正在聊的事。其余情况必须沉默，不要主动接话。"
    ),
    "very_low": (
        "你在群里的参与度是「极低」：只有被 @ 或被回复时才回复；其余任何情况都必须沉默。"
    ),
}

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


def reply_rules_section(req: Any) -> list[dict]:
    """L1 段：把当前回复模式的内置规则告诉模型（随回复设置动态变化）。"""
    getter = getattr(getattr(req, "config", None), "get", None)
    if not callable(getter):
        return []
    try:
        mode = str(getter("reply_mode", "legacy") or "legacy")
        group_enable = bool(getter("group_enable", False))
        private_enable = bool(getter("private_enable", True))
    except Exception:  # noqa: BLE001
        return []

    if mode == "participation":
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

    # 旧版：条件触发
    try:
        trigger = str(getter("trigger_mode", "at") or "at")
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


# ---------------- 预算 ----------------


class Budget:
    """每账号的 10 分钟请求/token 预算。"""

    def __init__(self) -> None:
        self.requests: deque[float] = deque()
        self.tokens: deque[tuple[float, int]] = deque()

    def _trim(self, now: float) -> None:
        while self.requests and now - self.requests[0] > BUDGET_WINDOW_SECONDS:
            self.requests.popleft()
        while self.tokens and now - self.tokens[0][0] > BUDGET_WINDOW_SECONDS:
            self.tokens.popleft()

    def allow(self, max_requests: int, max_tokens: int) -> bool:
        now = time.time()
        self._trim(now)
        used_tokens = sum(t for _, t in self.tokens)
        return len(self.requests) < max_requests and used_tokens < max_tokens

    def record(self, tokens: int = 0) -> None:
        now = time.time()
        self.requests.append(now)
        if tokens:
            self.tokens.append((now, max(0, int(tokens))))


def budget_of(runtime: Any) -> Budget:
    b = getattr(runtime, "participation_budget", None)
    if b is None:
        b = Budget()
        try:
            runtime.participation_budget = b
        except Exception:  # noqa: BLE001
            pass
    return b


# ---------------- 本地规则门 ----------------


def _is_at_me(event: Any, self_id: str) -> bool:
    for seg in getattr(event, "message", None) or []:
        if getattr(seg, "type", "") == "at":
            qq = str((getattr(seg, "data", {}) or {}).get("qq", ""))
            if qq in (self_id, "all", "0"):
                return True
    return False


def _reply_segment_id(event: Any) -> str:
    for seg in getattr(event, "message", None) or []:
        if getattr(seg, "type", "") == "reply":
            return str((getattr(seg, "data", {}) or {}).get("id") or "")
    return ""


def _is_reply_to_me(runtime: Any, event: Any, self_id: str) -> bool:
    """这条消息是不是**回复我**（而不是随便引用了别人）。

    reply 段只带目标 message_id，真正的作者要去会话历史里反查。查不到时按
    "不是我"保守处理——宁可少回一条，也不要在"仅被 @"的档位下乱接话。
    """
    target = _reply_segment_id(event)
    if not target:
        return False
    try:
        from app.agent.session.manager import SessionManager

        mgr = SessionManager(str(getattr(runtime, "bot_id", "")))
        history = mgr.get_history(_session_id(event), limit=mgr.MAX_HISTORY_MESSAGES)
        for item in history or []:
            if str(item.get("message_id") or "") != target:
                continue
            if str(item.get("role") or "") == "assistant":
                return True
            return str(item.get("user_id") or "") == self_id
    except Exception:  # noqa: BLE001 —— 反查失败按"不是我"，不阻断本轮
        return False
    return False


def _mentions_me_nickname(event: Any, nickname: str) -> bool:
    if not nickname:
        return False
    text = str(getattr(event, "text", "") or "")
    return bool(text and nickname in text)


def _bot_spoke_recently(runtime: Any, event: Any, within: int = 6) -> bool:
    """会话尾部最近 within 条里有没有 bot 自己说过话（判断"正在参与的话题"）。"""
    try:
        from app.agent.session.manager import SessionManager

        session_id = _session_id(event)
        mgr = SessionManager(str(getattr(runtime, "bot_id", "")))
        history = mgr.get_history(session_id, limit=within)
        return any(str(item.get("role")) == "assistant" for item in history or [])
    except Exception:  # noqa: BLE001
        return False


def _session_id(event: Any) -> str:
    if getattr(event, "event_type", "") == "message_group":
        gid = getattr(getattr(event, "group", None), "group_id", "")
        return f"group_{gid}"
    return f"private_{getattr(event, 'user_id', '')}"


def cooldown_ok(runtime: Any, event: Any, seconds: int) -> bool:
    """同群冷却**检查**（不写状态）。配合 `mark_request` 用：只有真请求才计时。

    旧实现把"检查"与"计时"揉在一行里，于是被门拒绝的消息也消耗冷却，
    真正 @ 到达时反而被挡（见 docs/plan/participation-reply-mode.md P0-6）。
    """
    if seconds <= 0:
        return True
    now = time.time()
    last = getattr(runtime, "participation_last_request", None) or {}
    key = _session_id(event)
    return now - float(last.get(key, 0) or 0) >= seconds


def mark_request(runtime: Any, event: Any) -> None:
    """记录"这个会话刚花了一次请求"（冷却计时起点）。"""
    now = time.time()
    last = getattr(runtime, "participation_last_request", None)
    if last is None:
        last = {}
        try:
            runtime.participation_last_request = last
        except Exception:  # noqa: BLE001 —— 只读运行时替身：冷却退化为"不生效"
            return
    last[_session_id(event)] = now


@dataclass(frozen=True)
class GateResult:
    """成本门的结论。``at_me``/``reply_me`` 同时供冷却豁免与"必须有发言"使用。"""

    allowed: bool
    reason: str
    at_me: bool = False
    reply_me: bool = False


def gate(runtime: Any, event: Any, level: str) -> GateResult:
    """本地**成本门**：True=值得花一次请求，False=只记录不请求。

    注意这不是"要不要发言"的语义判断——档位之间的差别写在系统提示词里
    （`LEVEL_PROMPT`）。这里只按档位给一个**成本**映射：档位越高，越愿意为
    普通消息付费。禁止往这里再加关键词/情绪之类的语义谓词。
    """
    self_id = str(getattr(event, "self_id", "") or "")
    at_me = _is_at_me(event, self_id)
    reply_me = _is_reply_to_me(runtime, event, self_id)
    if level == "very_low":
        return GateResult(at_me or reply_me, "at_or_reply" if (at_me or reply_me) else "level_very_low", at_me, reply_me)
    if level == "low":
        if at_me or reply_me:
            return GateResult(True, "at_or_reply", at_me, reply_me)
        return GateResult(False, "level_low", at_me, reply_me)
    if level == "medium":
        if at_me or reply_me or _bot_spoke_recently(runtime, event):
            return GateResult(True, "thread_continuation", at_me, reply_me)
        return GateResult(False, "level_medium", at_me, reply_me)
    if level == "high":
        return GateResult(True, "level_high", at_me, reply_me)
    if level == "very_high":
        return GateResult(True, "level_very_high", at_me, reply_me)
    return GateResult(False, "unknown_level", at_me, reply_me)


__all__ = [
    "BUDGET_WINDOW_SECONDS",
    "Budget",
    "DEFAULT_LEVEL",
    "DEFAULT_MAX_REQUESTS",
    "DEFAULT_MAX_TOKENS",
    "GateResult",
    "LEVELS",
    "LEVEL_PROMPT",
    "budget_of",
    "cooldown_ok",
    "gate",
    "mark_request",
    "reply_rules_section",
    "participation_section",
]
