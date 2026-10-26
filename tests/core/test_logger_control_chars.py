"""日志出口的控制字符消毒：终端响铃 / ANSI 注入的根因在这里。

背景：QQ 群名片可以带任意控制字符。机器人把收到的消息原样写日志，
``\\x07``(BEL) 会让 Windows Terminal 每收到一条消息就"叮"一声，而 ``\\x1b``
还能注入 ANSI 序列去改控制台颜色、伪造日志行。两者都必须在**格式化出口**
根治（一处收口，第三方日志也覆盖），而不是在下游一个个 QMessage 里转义。
"""

from __future__ import annotations

import logging

from app.core.logger import CallerModuleFormatter, sanitize_control


def _format(message: str, *args) -> str:
    formatter = CallerModuleFormatter("%(message)s")
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg=message, args=args or None, exc_info=None,
    )
    return formatter.format(record)


def test_bel_is_escaped_so_the_terminal_does_not_beep():
    out = _format("昵称\x07正文")

    assert "\x07" not in out
    assert "\\x07" in out


def test_ansi_escape_is_neutralised():
    out = _format("\x1b[31m红色\x1b[0m")

    assert "\x1b" not in out
    assert "\\x1b[31m" in out


def test_carriage_return_and_other_c0_are_escaped():
    out = _format("a\rb\x00c\x7f")

    for char in ("\r", "\x00", "\x7f"):
        assert char not in out
    assert "\\x0d" in out and "\\x00" in out and "\\x7f" in out


def test_newline_and_tab_are_preserved():
    out = _format("第一行\n第二行\t结尾")

    assert "\n" in out and "\t" in out


def test_normal_text_and_emoji_untouched():
    out = _format("正常的中文、emoji 😀、符号 ←→ ✓")

    assert out == "正常的中文、emoji 😀、符号 ←→ ✓"


def test_sanitize_control_is_idempotent():
    once = sanitize_control("a\x07b")

    assert sanitize_control(once) == once


def test_sanitize_handles_printf_style_args():
    out = _format("收到 %s 的消息", "某人\x07")

    assert "\x07" not in out
    assert "某人\\x07" in out
