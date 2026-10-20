"""逐请求结构化遥测的日志级别（``app/agent/loop/turn_metrics.py``）。

背景：``_log_request_line`` 每次真实请求都打一行 11 个字段的内部遥测
（``model= in= out= max_tokens= finish= text= reasoning= budget=``）。这行对"机器人回了
什么"零信息量，却因为级别是 INFO 而进 ``user.log``（用户简洁日志），把用户视角刷成
内部字段清单。**级别必须是 DEBUG**：排查时 debug.log 里一行不少，用户视角不再可见。

另外锁住两件事：

- 出错时才升级 WARNING —— 那时的字段就是排查入口，不能只留在 debug.log；
- ``max_tokens`` 的来源串必须是纯来源名，不能再被套一层
  ``max_tokens=1024（来自max_tokens=1024（来自provider 模型））``。
"""

import logging
from types import SimpleNamespace

import pytest

from app.agent.loop import turn_metrics
from app.agent.loop.turn_metrics import TurnBudget, _log_request_line


class _Recorder:
    """把 (级别, 文本) 记下来，替代真实 logger。"""

    def __init__(self) -> None:
        self.lines: list[tuple[int, str]] = []

    def info(self, msg):
        self.lines.append((logging.INFO, str(msg)))

    def warning(self, msg):
        self.lines.append((logging.WARNING, str(msg)))

    def error(self, msg):
        self.lines.append((logging.ERROR, str(msg)))

    def debug(self, msg):
        self.lines.append((logging.DEBUG, str(msg)))


@pytest.fixture()
def recorder(monkeypatch):
    rec = _Recorder()

    class _Logger:
        def add_info(self, _prefix):
            return rec

    monkeypatch.setattr(turn_metrics, "logger", _Logger())
    return rec


def _emit(**overrides):
    kwargs = {
        "session_id": "private_1",
        "round_index": 0,
        "served_model": "c68ae7ebc080",
        "max_tokens": 1024,
        "max_tokens_src": "provider 模型",
        "input_tokens": 22333,
        "output_tokens": 98,
        "input_estimate": 22333,
        "finish_reason": "stop",
        "text_chars": 39,
        "reasoning_chars": 272,
        "tools": 0,
        "error": "",
        "budget": TurnBudget.from_config({}),
    }
    kwargs.update(overrides)
    _log_request_line(SimpleNamespace(bot_id=3437542570), **kwargs)


def test_request_line_is_debug_not_info(recorder):
    _emit()

    assert recorder.lines, "应当有输出"
    level, text = recorder.lines[0]
    assert level == logging.DEBUG, "逐请求遥测必须是 DEBUG，否则会进 user.log 刷屏"
    assert "model=c68ae7ebc080" in text


def test_request_line_escalates_to_warning_on_error(recorder):
    _emit(error="未产出可用响应")

    level, text = recorder.lines[0]
    assert level == logging.WARNING
    assert "error=未产出可用响应" in text


def test_request_line_does_not_nest_max_tokens_origin(recorder):
    """来源串是纯名：不能再出现 ``（来自max_tokens=…`` 这种自套娃。"""
    _emit()

    _, text = recorder.lines[0]
    assert "max_tokens=1024(provider 模型)" in text
    assert text.count("max_tokens=") == 1


def test_debug_prompt_prints_json_not_python_repr(recorder):
    """``_log_debug_prompt`` 必须真走 ``json.dumps``。

    它一度漏了 ``import json``：异常被 except 兜住，静默退化成 ``str(messages)``，
    于是「调试 Prompt」打出来的是 Python repr（单引号、无法直接解析）。
    """
    messages = [{"role": "user", "content": "看图"}]

    turn_metrics._log_debug_prompt(SimpleNamespace(bot_id=1), "sess_1", messages, debug_enabled=True)

    level, text = recorder.lines[0]
    assert level == logging.INFO
    assert '"role": "user"' in text  # JSON 形态
    assert "'role'" not in text  # 不是 str(list) 的 Python repr
