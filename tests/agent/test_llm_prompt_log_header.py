"""``[Prompt]`` 调试头行：层摘要之后还要能回答"这轮 system 花了多少 token、前缀稳不稳"。

只在 ``ctx.state["debug_prompt"]`` 打开时打印（「LLM调试」模块的钩子写这个标记），
所以这里用一个假的 logger 收集日志文本，直接断言头行的内容与顺序。
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


def _messages() -> list[dict]:
    return [
        {"role": "system", "content": "x" * 200},
        {"role": "user", "content": "你好"},
    ]


def test_header_has_layers_tokens_and_prefix(captured):
    ctx = _ctx(debug_prompt=True, assembly_layers="L1 系统提示词: system_prompt(200)")
    turn_metrics.log_debug_prompt_if_enabled(
        SimpleNamespace(bot_id="1"), "group_1", _messages(), ctx
    )

    header = next(line for line in captured.lines if line.startswith("[Prompt] group_1"))
    assert "L1 系统提示词" in header
    assert "system≈" in header and "tok" in header
    assert "prefix=" in header
    # 头行在全文之前（全文是 JSON 数组，以 "[" 开头）
    assert captured.lines.index(header) == 0


def test_no_header_without_layer_summary(captured):
    """没有装配摘要（老路径/直调）时也要打全文，只是不打头行。"""
    turn_metrics.log_debug_prompt_if_enabled(
        SimpleNamespace(bot_id="1"), "group_1", _messages(), _ctx(debug_prompt=True)
    )
    assert all(not line.startswith("[Prompt] group_1 ·") for line in captured.lines)
    # 全文那一条以 "[Prompt] {session}" 开头、紧跟 JSON 数组
    assert any(line.startswith("[Prompt] group_1") for line in captured.lines)


def test_switched_off_logs_nothing(captured):
    turn_metrics.log_debug_prompt_if_enabled(
        SimpleNamespace(bot_id="1"), "group_1", _messages(), _ctx(debug_prompt=False)
    )
    assert captured.lines == []


def test_over_budget_is_flagged(captured):
    """超预算只提示、不裁剪：标记要显眼（说明"这轮固定成本很高"）。"""
    big = [{"role": "system", "content": "字" * (turn_metrics.SYSTEM_TOKEN_WARN * 2 + 100)}]
    ctx = _ctx(debug_prompt=True, assembly_layers="L1 系统提示词: system_prompt(1)")
    turn_metrics.log_debug_prompt_if_enabled(SimpleNamespace(bot_id="1"), "group_1", big, ctx)

    header = next(line for line in captured.lines if line.startswith("[Prompt] group_1"))
    assert "⚠超预算" in header
