"""高危工具审批（P6）：**fail-closed** 是全部重点。

验四件事：

1. 没开开关 → 不问、直接执行（老行为）；
2. 开了开关但**没有通道** → 拒绝（不是"放行"）；
3. 通道批准 → 执行；拒绝/超时/通道异常 → 都不执行；
4. 只有 OneBot 的高危工具会触发审批（普通工具、系统工具不受影响）。
"""

from __future__ import annotations

import types

import pytest

from app.agent.tools.approval import (
    SENSITIVE_LEVELS,
    ApprovalOutcome,
    approval_enabled,
    needs_approval,
    request_approval,
)
from app.agent.tools.spec import ToolSpec, make_executor


class _Channel:
    def __init__(self, outcome=None, *, raises: Exception | None = None):
        self.outcome = outcome
        self.raises = raises
        self.asked: list[tuple[str, dict]] = []

    async def ask(self, runtime, *, tool, args, ctx=None):
        self.asked.append((tool, dict(args or {})))
        if self.raises is not None:
            raise self.raises
        return self.outcome


def _runtime(tmp_path, monkeypatch, *, approval: bool, channel=None):
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    return types.SimpleNamespace(
        bot_id=778,
        config={"tool_approval_enable": approval, "tool_spill_enable": False},
        telemetry=None,
        llm_tool_call_hooks=None,
        approval_channel=channel,
    )


@pytest.fixture(autouse=True)
def _gate_open(monkeypatch):
    """审批测试只关心闸门，不关心"模块是否装了"：执行前复核统一放行。"""
    monkeypatch.setattr("app.agent.tools.spec._execution_gate", lambda ctx, spec: "")


def _ctx(runtime):
    return types.SimpleNamespace(runtime=runtime, event=None, session_id="group_1", bot=None)


def _spec(handler, *, source="onebot", sensitivity="high", name="danger") -> ToolSpec:
    return ToolSpec(
        name=name,
        description="",
        parameters={"type": "object", "properties": {}},
        handler=handler,
        scopes=("*",),
        source=source,
        sensitivity=sensitivity,
    )


async def _run(spec, runtime) -> str:
    return await make_executor([spec], _ctx(runtime))(spec.name, {})


def test_only_onebot_sensitive_tools_need_approval(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path, monkeypatch, approval=True)

    assert needs_approval(runtime, _spec(lambda: None)) is True
    assert needs_approval(runtime, _spec(lambda: None, source="system")) is False
    assert needs_approval(runtime, _spec(lambda: None, sensitivity="normal")) is False
    assert SENSITIVE_LEVELS == {"high", "critical"}

    off = _runtime(tmp_path, monkeypatch, approval=False)
    assert approval_enabled(off) is False
    assert needs_approval(off, _spec(lambda: None)) is False


async def test_no_channel_means_rejected(tmp_path, monkeypatch):
    """没有通道 = 拒绝；这是 fail-closed 的核心断言。"""
    called: list[str] = []

    async def _handler(ctx, args):
        called.append("ran")
        return "执行了"

    runtime = _runtime(tmp_path, monkeypatch, approval=True, channel=None)

    assert await request_approval(runtime, tool="danger", args={}) is ApprovalOutcome.UNAVAILABLE
    result = await _run(_spec(_handler), runtime)

    assert called == [], "没有通道时绝不能真的执行"
    assert result.startswith("error:") and "未获批准" in result


async def test_approved_runs_exactly_once(tmp_path, monkeypatch):
    called: list[str] = []

    async def _handler(ctx, args):
        called.append("ran")
        return "执行了"

    channel = _Channel(ApprovalOutcome.ALLOWED_ONCE)
    runtime = _runtime(tmp_path, monkeypatch, approval=True, channel=channel)

    result = await _run(_spec(_handler), runtime)

    assert called == ["ran"]
    assert result.startswith("执行了")
    assert [tool for tool, _ in channel.asked] == ["danger"]


async def test_rejected_and_timeout_paths_do_not_run(tmp_path, monkeypatch):
    for outcome in (ApprovalOutcome.REJECTED, ApprovalOutcome.TIMEOUT):
        called: list[str] = []

        async def _handler(ctx, args, _called=called):
            _called.append("ran")
            return "执行了"

        runtime = _runtime(
            tmp_path, monkeypatch, approval=True, channel=_Channel(outcome)
        )
        result = await _run(_spec(_handler), runtime)

        assert called == [], f"{outcome} 不该执行"
        assert "未获批准" in result


async def test_channel_exception_is_treated_as_unavailable(tmp_path, monkeypatch):
    called: list[str] = []

    async def _handler(ctx, args):
        called.append("ran")
        return "执行了"

    runtime = _runtime(
        tmp_path, monkeypatch, approval=True, channel=_Channel(raises=RuntimeError("通道炸了"))
    )

    assert await request_approval(runtime, tool="danger", args={}) is ApprovalOutcome.UNAVAILABLE
    assert called == []


async def test_switch_off_skips_the_channel_entirely(tmp_path, monkeypatch):
    channel = _Channel(ApprovalOutcome.REJECTED)
    runtime = _runtime(tmp_path, monkeypatch, approval=False, channel=channel)

    async def _handler(ctx, args):
        return "照常执行"

    result = await _run(_spec(_handler), runtime)

    assert result.startswith("照常执行")
    assert channel.asked == [], "开关关着时不该打扰任何人"


async def test_non_sensitive_onebot_tool_never_asks(tmp_path, monkeypatch):
    channel = _Channel(ApprovalOutcome.REJECTED)
    runtime = _runtime(tmp_path, monkeypatch, approval=True, channel=channel)

    async def _handler(ctx, args):
        return "读操作"

    result = await _run(_spec(_handler, sensitivity="normal"), runtime)

    assert result.startswith("读操作")
    assert channel.asked == []
