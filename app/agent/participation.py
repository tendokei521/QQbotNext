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


def participation_section(req: Any) -> list[dict]:
    """L1 段：参与度约定（仅新版模式生效）。"""
    getter = getattr(getattr(req, "config", None), "get", None)
    if not callable(getter):
        return []
    try:
        if str(getter("reply_mode", "legacy") or "legacy") != "participation":
            return []
        level = str(getter("participation_level", DEFAULT_LEVEL) or DEFAULT_LEVEL)
    except Exception:  # noqa: BLE001
        return []
    text = LEVEL_PROMPT.get(level, LEVEL_PROMPT[DEFAULT_LEVEL]) + SILENCE_RULE
    return [{"role": "system", "content": "### 群聊参与度\n" + text}]


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


def _is_reply_to_me(event: Any, self_id: str) -> bool:
    # reply 段不带作者信息，极低/低等级下先放行（让模型用引用内容判断）
    for seg in getattr(event, "message", None) or []:
        if getattr(seg, "type", "") == "reply":
            return True
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


def gate(runtime: Any, event: Any, level: str) -> tuple[bool, str]:
    """本地规则门：True=进入 LLM，False=只记录不请求。"""
    self_id = str(getattr(event, "self_id", "") or "")
    at_me = _is_at_me(event, self_id)
    reply_me = _is_reply_to_me(event, self_id)
    if level == "very_low":
        return (at_me or reply_me, "at_or_reply" if (at_me or reply_me) else "level_very_low")
    if level == "low":
        if at_me or reply_me:
            return True, "at_or_reply"
        return (False, "level_low")
    if level == "medium":
        if at_me or reply_me or _bot_spoke_recently(runtime, event):
            return True, "thread_continuation"
        return (False, "level_medium")
    if level == "high":
        return True, "level_high"
    if level == "very_high":
        return True, "level_very_high"
    return (False, "unknown_level")


async def observe_message(runtime: Any, event: Any, config: Any) -> bool:
    """新版：先把消息写进会话（无论最终是否回复）。返回是否真的写入。"""
    try:
        from app.agent.loop.turn_input import (
            TurnInput,
            get_or_create_session,
            record_user_turn,
        )
        from app.agent.session.manager import SessionManager

        raw = str(getattr(event, "text", "") or "").strip()
        if not raw and not getattr(event, "message", None):
            return False
        is_private = getattr(event, "event_type", "") != "message_group"
        group = getattr(event, "group", None)
        turn = TurnInput(
            is_private=is_private,
            session_id=_session_id(event),
            user_id=str(getattr(event, "user_id", "") or ""),
            group_id=None if is_private else str(getattr(group, "group_id", "") or ""),
            user_text=raw,
            raw_user_text=raw,
            segment_text=raw,
            include_pre_history=False,
        )
        mgr = SessionManager(str(getattr(runtime, "bot_id", "")))
        session = await get_or_create_session(mgr, turn, config, None)
        if session is None:
            return False
        user = getattr(event, "user", None)
        nickname = (getattr(user, "card", "") or getattr(user, "nickname", "") or "") if user else ""
        await record_user_turn(runtime, event, turn, config, mgr, nickname=nickname)
        return True
    except Exception as e:  # noqa: BLE001 —— 观察写入失败不该阻断整轮
        from app.agent import logger

        logger.debug(f"[参与度] 全量写入失败（已忽略）: {e}")
        return False


__all__ = [
    "BUDGET_WINDOW_SECONDS",
    "Budget",
    "DEFAULT_LEVEL",
    "DEFAULT_MAX_REQUESTS",
    "DEFAULT_MAX_TOKENS",
    "LEVELS",
    "LEVEL_PROMPT",
    "budget_of",
    "gate",
    "participation_section",
    "observe_message",
]
