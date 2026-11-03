"""assistant 历史按回复模式渲染（2026-11-03）。

线上现象：参与度模式（发言通道）下，历史里 assistant 是裸正文，模型照抄
"直接说话"的历史，于是下一轮只输出文本、不调 `send_message`（本地日志里
`[Response] 工具 0 个` + 只写入 user 条目）。这里锁两条口径：

1. `legacy`（文本即回复）：assistant 仍是不带标记的裸正文；
2. `participation`（发言通道）：assistant 渲染成 `[tool_call:send_message]{...}`，
   让历史与「发言要调工具」的协议一致。
"""

from __future__ import annotations

import json

from app.agent.context.group import format_history_for_llm
from app.agent.message_assembly import history as history_assembly
from app.agent.session.model import BaseInfo, HistoryEntry

REPLY = "在、在的……老师有什么事吗？"


def _entry() -> HistoryEntry:
    return HistoryEntry(
        role="assistant",
        message_id="228914218",
        base=BaseInfo(text=REPLY, time=1788342260, group_id="466052056"),
    )


def _render(mode: str, **kwargs) -> str:
    return history_assembly.render_history_entry(_entry(), reply_mode=mode, **kwargs) or ""


def test_legacy_mode_keeps_plain_assistant_text():
    assert _render("legacy") == REPLY


def test_participation_mode_renders_tool_call():
    text = _render("participation")
    assert text.startswith(history_assembly.SPEECH_TOOL_CALL_MARKER)
    payload = json.loads(text[len(history_assembly.SPEECH_TOOL_CALL_MARKER):])
    assert payload == {"messages": REPLY}


def test_format_history_for_llm_passes_reply_mode():
    entries = [_entry()]
    legacy = format_history_for_llm(
        entries, renderer=history_assembly.render_history_entry, reply_mode="legacy"
    )
    speech = format_history_for_llm(
        entries, renderer=history_assembly.render_history_entry, reply_mode="participation"
    )
    assert legacy[0]["content"] == REPLY
    assert speech[0]["content"].startswith(history_assembly.SPEECH_TOOL_CALL_MARKER)


def test_default_renderer_tolerates_reply_mode_kwarg():
    """未接管 history_renderer 时走 model 的默认渲染器，也不能因多传参数崩掉。"""
    rendered = format_history_for_llm([_entry()], reply_mode="participation")
    assert rendered and rendered[0]["role"] == "assistant"
    assert REPLY in rendered[0]["content"]
