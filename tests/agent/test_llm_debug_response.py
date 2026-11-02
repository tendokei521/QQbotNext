"""「LLM调试」的模型返回日志（``[Response]``）：开关开着一轮结束后要能看到"回了什么"。

背景：``[Prompt]`` 只能看到我们发出去的内容；发言通道下模型正文只是思考（不会发出），
"模型写了正文却没调 send_message"这类问题必须能看到返回原文才能定位。这里锁定：
①开关由 ``ctx.state["debug_response"]`` 控制、与 ``debug_prompt`` 相互独立；
②正文/思考/工具调用都要打出来（发话靠工具时，只打正文等于什么都没打）；
③空正文要有明确占位（不是空白，避免误判成没打）；
④超长内容截断并标注。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent.loop import turn_metrics


class _Log:
    """收集 ``logger.add_info(...).info(msg)`` 的替身。"""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def add_info(self, _label):  # noqa: ANN001, ANN202
        return self

    def info(self, message: str) -> None:
        self.lines.append(str(message))

    def debug(self, message: str) -> None:
        self.lines.append(str(message))


@pytest.fixture()
def captured(monkeypatch):
    log = _Log()
    monkeypatch.setattr(turn_metrics, "logger", log)
    return log


def _ctx(**state):
    return SimpleNamespace(state=dict(state))


def _call(ctx, **kwargs):
    turn_metrics.log_debug_response_if_enabled(
        SimpleNamespace(bot_id="1"), "group_1", ctx=ctx, **kwargs
    )


def test_switched_off_logs_nothing(captured):
    """没开「记录模型返回」时一条都不打（与 debug_prompt 互不影响）。"""
    _call(_ctx(debug_prompt=True), text="你好", reason="completed")
    assert captured.lines == []


def test_logs_text_reasoning_and_tool_calls(captured):
    text = "在的，老师……怎么了？"
    _call(
        _ctx(debug_response=True),
        text=text,
        reasoning="老师叫我了，先应一声。",
        tool_calls=[{"name": "send_message", "args": {"messages": "在的"}}],
        reason="completed",
        rounds=1,
        usage={"total_tokens": 123},
    )

    assert len(captured.lines) == 1
    line = captured.lines[0]
    assert line.startswith("[Response] group_1")
    # 头行给"一眼能扫"的摘要
    assert "结局=completed" in line and "轮次=1" in line
    assert f"正文 {len(text)} 字" in line and "工具 1 个" in line
    # JSON 里连思考与工具调用一起出现（发言通道下正文只是思考）
    assert "在的，老师……怎么了？" in line
    assert "老师叫我了" in line
    assert "send_message" in line
    assert '"total_tokens": 123' in line


def test_empty_text_has_explicit_placeholder(captured):
    """正文为空是常态（模型只调工具），必须显式写出来而不是留白。"""
    _call(_ctx(debug_response=True), text="", reason="silent")
    line = captured.lines[0]
    assert "（空：模型这次没有输出正文）" in line
    assert "正文 0 字" in line


def test_long_content_is_truncated(captured):
    big = "字" * (turn_metrics.DEBUG_RESPONSE_MAX_CHARS + 50)
    _call(_ctx(debug_response=True), text=big, reason="completed")
    line = captured.lines[0]
    assert "已截断" in line
    assert f"原长 {len(big)} 字符" in line


def test_bad_tool_args_do_not_break_logging(captured):
    """工具参数不可 JSON 序列化时退化成 str，日志不能因此丢失。"""

    class _Weird:
        def __str__(self) -> str:
            return "weird-args"

    _call(
        _ctx(debug_response=True),
        text="x",
        tool_calls=[{"name": "t", "args": {"bad": _Weird()}}],
        reason="completed",
    )
    assert "weird-args" in captured.lines[0]
