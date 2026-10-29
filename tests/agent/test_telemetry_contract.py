"""遥测契约：**两条收尾路径**与 `TelemetryRecorder.record_call_simple` 的签名必须对齐。

## 为什么单独一个文件

线上事故：`send_message`（发言通道）静默收尾后，流水线报
``TelemetryRecorder.record_call_simple() got an unexpected keyword argument 'reason'``，
整轮被记成「任务异常」——用户那边已经处理完，却凭空多一条 ERROR。

根因不是某个调用点写错，而是**所有测试都把遥测换成 ``record_call_simple(**kwargs)``
的宽容替身**：调用点传什么关键字都能收，真实现少一个字段永远测不出来。这个文件用
**真 recorder + 真调用点**钉住契约，并钉住"遥测失败不许影响一轮"的兜底。
"""

from __future__ import annotations

import time
from types import SimpleNamespace

from app.agent.loop.turn import _record_telemetry
from app.agent.loop.turn_metrics import _record_stream_telemetry
from app.agent.loop.turn_runner import TurnReason
from app.agent.telemetry import TelemetryRecorder


def _runtime(recorder=None) -> SimpleNamespace:
    return SimpleNamespace(bot_id="1", telemetry=recorder or TelemetryRecorder())


def test_record_call_simple_accepts_reason_and_exposes_it():
    t = TelemetryRecorder()
    t.record_call_simple(provider="openai", model="m", success=True, reason="silent")

    record = t.recent(1)[0]
    assert record["reason"] == "silent"


def test_blocking_turn_records_reason_through_real_recorder():
    """非流式（`run_turn`）收尾：真 recorder 必须接得住 ``reason=TurnReason``。"""
    rt = _runtime()
    _record_telemetry(
        rt,
        session_id="private_1",
        provider_chain=[{"provider": "openai", "model": "m"}],
        model="m",
        messages=[{"role": "user", "content": "hi"}],
        text="",
        tool_results=[],
        started=time.monotonic(),
        usage={"prompt_tokens": 3, "completion_tokens": 0},
        stream=False,
        reason=TurnReason.SILENT,
    )

    records = rt.telemetry.recent(1)
    assert len(records) == 1
    assert records[0]["reason"] == "silent"


def test_stream_turn_records_reason_through_real_recorder():
    """流式（`stream_response`）收尾：同一契约，且不再抛 TypeError。"""
    rt = _runtime()
    _record_stream_telemetry(
        rt,
        "group_1",
        [{"provider": "openai", "model": "m"}],
        "m",
        [{"role": "user", "content": "hi"}],
        [],
        [],
        time.monotonic(),
        success=True,
        usage={"prompt_tokens": 4, "completion_tokens": 2},
        reason=str(TurnReason.COMPLETED),
    )

    records = rt.telemetry.recent(1)
    assert len(records) == 1
    assert records[0]["reason"] == "completed"
    assert records[0]["stream"] is True


class _RejectingRecorder:
    """模拟"遥测实现比调用点旧"：任何 reason 关键字都直接 TypeError。"""

    def record_call_simple(self, **kwargs):
        if "reason" in kwargs:
            raise TypeError("unexpected keyword argument 'reason'")


def test_stream_telemetry_failure_never_aborts_the_turn():
    """遥测是旁路：实现漂移导致的异常必须被吞掉，不能冒泡成流水线任务异常。"""
    _record_stream_telemetry(
        _runtime(_RejectingRecorder()),
        "group_1",
        [],
        "m",
        [],
        ["hi"],
        [],
        time.monotonic(),
        success=True,
        reason="completed",
    )  # 不抛 = 通过
