"""section 注册表的对外行为：顺序插位 / 同名遮蔽 / complete 冲突 / 卸载回退。

只断言可观察的行为：
- 注册的段落按 order 插到正确位置（不比对完整默认清单快照）；
- agent 作用域遮蔽同名 global 段，卸载后 global 版本重新可见；
- 两个 complete 段冲突时抛错并留日志，单段 complete 正常渲染；
- 单段构建失败只跳过该段，不影响其余段。
"""

from __future__ import annotations

import types

import pytest

from app.agent.assembly import sections as sections_mod
from app.agent.assembly.assembler import (
    PromptAssembler,
    PromptRequest,
    describe,
    resolve_blocks,
)
from app.agent.assembly.sections import (
    SCOPE_AGENT,
    SCOPE_GLOBAL,
    Section,
    SectionRegistry,
    register_section,
    registry_of,
    resolve_sections,
    unregister_section,
)


def _runtime() -> types.SimpleNamespace:
    """无副作用的 runtime 替身：注册表跟着它走，但不需要真实 Agent 运行时。"""
    return types.SimpleNamespace(bot_id=778)


def _section(name: str, order: int, text: str = "", **kw) -> Section:
    def builder(req):  # noqa: ANN001, ANN202
        return [{"role": "system", "content": text}] if text else []

    return Section(name, order, builder, **kw)


def _names(sections) -> list[str]:
    return [s.name for s in sections]


# ==================== 1. 默认注册表 ====================


def test_registry_of_hangs_registry_on_runtime_and_caches_it():
    runtime = _runtime()
    first = registry_of(runtime)
    second = registry_of(runtime)
    assert first is second
    assert runtime.prompt_sections is first
    assert resolve_sections(runtime)


def test_process_default_registry_is_isolated_from_bot_registries(monkeypatch):
    """没有 runtime 的直调用进程表；bot runtime 上注册的段不会漏进直调路径。"""
    monkeypatch.setattr(sections_mod, "_PROCESS_DEFAULT", None)
    runtime = _runtime()
    register_section(runtime, _section("bot_only", 55, "x"))

    assert "bot_only" in _names(resolve_sections(runtime))
    assert "bot_only" not in _names(resolve_sections(None))


def test_two_bot_runtimes_do_not_share_sections():
    a, b = _runtime(), _runtime()
    register_section(a, _section("only_a", 55, "A"))
    assert "only_a" in _names(resolve_sections(a))
    assert "only_a" not in _names(resolve_sections(b))


# ==================== 2. order=55 落在 proactive 与 message_meta 之间 ====================


def test_order_55_section_lands_between_proactive_and_message_meta():
    runtime = _runtime()
    register_section(runtime, _section("between", 55, "### 插进来的段"))

    names = _names(resolve_sections(runtime))
    assert names.index("between") == names.index("proactive") + 1
    assert names.index("between") + 1 == names.index("message_meta")

    # 装配结果里也真的落在两者之间（describe 的空段也留行，位置可断言）
    rows = describe(PromptRequest(runtime=runtime, config={"system_prompt": "人设"}, user_text="hi"))
    blocks = [row["block"] for row in rows]
    assert blocks.index("between") == blocks.index("proactive") + 1
    assert blocks.index("between") + 1 == blocks.index("message_meta")
    assert any(row["block"] == "between" and row["chars"] == len("### 插进来的段") for row in rows)


def test_resolve_blocks_keeps_extra_between_group_log_and_history():
    """兼容的 extra 通道仍按老位置渲染（extra 不是默认段，但每次渲染都插回）。"""
    runtime = _runtime()
    names = _names(resolve_blocks(PromptRequest(runtime=runtime)))
    assert names.index("extra") == names.index("group_log") + 1
    assert names.index("extra") + 1 == names.index("history")


def test_plugin_registered_extra_overrides_compat_channel():
    """插件注册同名 ``extra`` 段时以插件为准，不会插出两条。"""
    runtime = _runtime()
    register_section(runtime, _section("extra", 105, "插件版额外块"))
    names = _names(resolve_blocks(PromptRequest(runtime=runtime)))
    assert names.count("extra") == 1


# ==================== 3. agent 遮蔽同名 global ====================


def test_agent_scope_shadows_same_named_global_section():
    registry = SectionRegistry()
    registry.add(_section("demo", 35, "GLOBAL 版", scope=SCOPE_GLOBAL))
    registry.add(_section("demo", 35, "AGENT 版", scope=SCOPE_AGENT))

    resolved = registry.resolve(scope=SCOPE_AGENT)
    demos = [s for s in resolved if s.name == "demo"]
    assert len(demos) == 1
    assert demos[0].scope == SCOPE_AGENT

    # global 视图只看 global 段：agent 段不参与，也不反向遮蔽
    globals_only = registry.resolve(scope=SCOPE_GLOBAL)
    global_demos = [s for s in globals_only if s.name == "demo"]
    assert len(global_demos) == 1
    assert global_demos[0].scope == SCOPE_GLOBAL


def test_agent_scope_shadowing_also_works_through_runtime_registry():
    runtime = _runtime()
    register_section(runtime, _section("demo", 35, "GLOBAL 版", scope=SCOPE_GLOBAL))
    assert _names(resolve_sections(runtime)).count("demo") == 1

    register_section(runtime, _section("demo", 35, "AGENT 版", scope=SCOPE_AGENT))
    resolved = [s for s in resolve_sections(runtime) if s.name == "demo"]
    assert len(resolved) == 1
    assert resolved[0].scope == SCOPE_AGENT

    # 撤掉 agent 覆盖后，global 版本重新可见
    assert unregister_section(runtime, "demo", scope=SCOPE_AGENT) == 1
    resolved = [s for s in resolve_sections(runtime) if s.name == "demo"]
    assert len(resolved) == 1
    assert resolved[0].scope == SCOPE_GLOBAL


# ==================== 4. complete 段冲突 ====================


def test_two_complete_sections_raise_value_error():
    registry = SectionRegistry()
    registry.add(_section("whole_a", 55, "A", complete=True))
    registry.add(_section("whole_b", 56, "B", complete=True))

    with pytest.raises(ValueError, match="complete"):
        registry.resolve()


def test_single_complete_section_is_allowed_and_rendered():
    runtime = _runtime()
    register_section(runtime, _section("whole", 55, "整条 system", complete=True))
    resolved = resolve_sections(runtime)
    assert [s.name for s in resolved if s.complete] == ["whole"]

    messages = PromptAssembler().build(
        PromptRequest(runtime=runtime, config={"system_prompt": "人设"}, user_text="hi")
    )
    assert "整条 system" in [m["content"] for m in messages if m["role"] == "system"]


def test_complete_conflict_aborts_build_and_logs_error(monkeypatch):
    """装配失败要留痕并抛错，不能悄悄发出畸形 prompt。"""
    runtime = _runtime()
    register_section(runtime, _section("whole_a", 55, "A", complete=True))
    register_section(runtime, _section("whole_b", 56, "B", complete=True))

    from app.agent.assembly import assembler as assembler_mod

    errors: list[str] = []

    class _Recorder:
        def add_info(self, info):  # noqa: ANN001, ANN202
            return self

        def error(self, msg):  # noqa: ANN001, ANN202
            errors.append(str(msg))

        def warning(self, msg):  # noqa: ANN001, ANN202
            errors.append(str(msg))

    monkeypatch.setattr(assembler_mod, "logger", _Recorder())

    with pytest.raises(ValueError, match="complete"):
        PromptAssembler().build(PromptRequest(runtime=runtime, config={"system_prompt": "人设"}))
    assert any("提示词段解析失败" in msg for msg in errors)


def test_builder_failure_skips_section_and_logs_error(monkeypatch):
    """单段失败跳过该段 + error 留痕（理由见 build_all_blocks docstring）。"""
    runtime = _runtime()

    def boom(req):  # noqa: ANN001, ANN202
        raise RuntimeError("记忆召回炸了")

    register_section(runtime, Section("boom_section", 55, boom))

    from app.agent.assembly import assembler as assembler_mod

    errors: list[str] = []

    class _Recorder:
        def add_info(self, info):  # noqa: ANN001, ANN202
            return self

        def error(self, msg):  # noqa: ANN001, ANN202
            errors.append(str(msg))

        def warning(self, msg):  # noqa: ANN001, ANN202
            errors.append(str(msg))

    monkeypatch.setattr(assembler_mod, "logger", _Recorder())

    messages = PromptAssembler().build(PromptRequest(runtime=runtime, config={"system_prompt": "人设"}))
    assert messages[0] == {"role": "system", "content": "人设"}  # 其它段照常
    assert any("boom_section" in msg and "构建失败" in msg for msg in errors)


def test_section_validation():
    with pytest.raises(ValueError):
        Section("   ", 10, lambda req: [])
    with pytest.raises(ValueError):
        Section("x", "not-an-int", lambda req: [])
    with pytest.raises(ValueError):
        Section("x", 10, lambda req: [], scope="nope")


# ==================== 5. 卸载后回到默认 ====================


def test_unregister_removes_added_section_and_is_idempotent():
    runtime = _runtime()
    baseline = _names(resolve_sections(runtime))

    register_section(runtime, _section("temp", 55, "临时段"))
    assert "temp" in _names(resolve_sections(runtime))

    assert unregister_section(runtime, "temp") == 1
    assert _names(resolve_sections(runtime)) == baseline

    # 再撤一次：没东西可撤，返回 0（幂等）
    assert unregister_section(runtime, "temp") == 0


def test_unregister_override_restores_builtin_section():
    """覆盖默认段后卸载 → 恢复内置版本，默认段不会因一次覆盖永久消失。"""
    runtime = _runtime()
    register_section(runtime, _section("memory", 80, "插件版记忆"))
    memory_row = [
        row for row in describe(PromptRequest(runtime=runtime, config={"system_prompt": "s"}))
        if row["block"] == "memory"
    ][0]
    assert memory_row["chars"] == len("插件版记忆")

    assert unregister_section(runtime, "memory") == 1
    memory_row = [
        row for row in describe(PromptRequest(runtime=runtime, config={"system_prompt": "s"}))
        if row["block"] == "memory"
    ][0]
    assert memory_row["chars"] != len("插件版记忆"), "卸载覆盖后应回到内置段"
