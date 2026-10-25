"""能力声明（P6 第 2 步）：三个开关变成"谁提供什么"。

验四件事：

1. `OutputCapability` 三选一、非法值直接拒绝；配置推导与改造前一致（speech 优先）；
2. 声明**优先于**配置：模块声明流式时，即使配置关着也走流式；
3. 撤销声明后回落到配置（升级平滑，不会突然不会回话）；
4. `context_expand` 由 `llm_context` 声明；没声明时回落到配置键。
"""

from __future__ import annotations

import types

import pytest

from app.agent.capabilities import (
    CAP_CONTEXT_EXPAND,
    OUTPUT_BLOCKING,
    OUTPUT_SPEECH,
    OUTPUT_STREAM,
    CapabilityRegistry,
    OutputCapability,
    has_tool_capability,
    output_capability,
    registry_of,
)


def _runtime(**config):
    return types.SimpleNamespace(bot_id=1, config=config, capabilities=CapabilityRegistry())


# ── 输出能力 ──────────────────────────────────────────


def test_only_three_kinds_are_accepted():
    assert OutputCapability(OUTPUT_STREAM).streams is True
    assert OutputCapability(OUTPUT_SPEECH).streams is True, "发言通道也是边产边发"
    assert OutputCapability(OUTPUT_BLOCKING).streams is False
    with pytest.raises(ValueError):
        OutputCapability("turbo")


def test_config_derivation_matches_the_old_switch_behaviour():
    assert OutputCapability.from_config({"stream_output": True}).kind == OUTPUT_STREAM
    assert OutputCapability.from_config({"stream_output": False}).kind == OUTPUT_BLOCKING
    # 发言通道优先：它才是真正决定产出形态的那个
    assert OutputCapability.from_config(
        {"stream_output": True, "speech_via_tool": True}
    ).kind == OUTPUT_SPEECH
    assert OutputCapability.from_config(None).kind == OUTPUT_BLOCKING


def test_declaration_wins_over_config():
    """这就是这一步的目的：能力由声明决定，配置只是偏好。"""
    runtime = _runtime(stream_output=False)
    registry = registry_of(runtime)
    registry.declare_output(OUTPUT_STREAM, source="llm_output")

    assert output_capability(runtime).kind == OUTPUT_STREAM, "声明优先，配置关着也流式"

    registry.revoke_output(source="llm_output")
    assert output_capability(runtime).kind == OUTPUT_BLOCKING, "撤销后回落到配置"


def test_revoke_only_works_for_the_declaring_source():
    runtime = _runtime()
    registry = registry_of(runtime)
    registry.declare_output(OUTPUT_SPEECH, source="llm_output")

    registry.revoke_output(source="someone_else")

    assert registry.output is not None, "别人不能撤销你声明的能力"


def test_select_default_output_uses_the_declaration():
    from app.agent.delivery.output import BlockingOutput, StreamOutput, select_default_output

    runtime = _runtime(stream_output=False)
    registry_of(runtime).declare_output(OUTPUT_STREAM, source="llm_output")

    assert isinstance(select_default_output(runtime.config, runtime), StreamOutput)

    registry_of(runtime).revoke_output(source="llm_output")
    assert isinstance(select_default_output(runtime.config, runtime), BlockingOutput)


# ── 工具能力 ──────────────────────────────────────────


def test_context_expand_capability_gates_the_tool_prereq():
    from app.agent.tools.policy import prereq_state

    runtime = _runtime(context_expand_enable=False)

    ready, _ = prereq_state(runtime, "expand_recent")
    assert ready is False, "没人声明 + 配置关着 = 不可用（与改造前一致）"

    registry_of(runtime).declare_tool(CAP_CONTEXT_EXPAND, source="llm_context")
    ready, _ = prereq_state(runtime, "expand_recent")
    assert ready is True, "模块声明提供该能力后，工具就绪"

    registry_of(runtime).revoke_tool(CAP_CONTEXT_EXPAND, source="llm_context")
    assert prereq_state(runtime, "expand_recent")[0] is False


def test_tool_capability_falls_back_to_the_config_key():
    runtime = _runtime(context_expand_enable=True)

    assert has_tool_capability(runtime, CAP_CONTEXT_EXPAND, config_key="context_expand_enable")
    assert registry_of(runtime).has_tool(CAP_CONTEXT_EXPAND) is False


def test_registry_is_idempotent_and_enumerable():
    registry = CapabilityRegistry()
    registry.declare_tool(CAP_CONTEXT_EXPAND, source="a")
    registry.declare_tool(CAP_CONTEXT_EXPAND, source="b")

    caps = registry.tool_capabilities()
    assert list(caps) == [CAP_CONTEXT_EXPAND]
    assert caps[CAP_CONTEXT_EXPAND].source == "b", "后声明者覆盖（最后装的模块说话）"
