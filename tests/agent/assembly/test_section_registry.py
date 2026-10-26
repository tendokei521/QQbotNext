"""section 注册表：顺序 / 遮蔽 / complete 冲突 / 卸载 / 与改造前逐字节一致。

Q4 的安全网有两层：

1. ``tests/agent/assembly`` 既有 164 条输出形态测试（改造前后都跑，锁住第三方可见行为）；
2. 本文件第 6 组用例：直接从 git 历史里取出**改造前**的 ``assembler.py``，在临时目录
   import 它，与当前实现喂同一份代表性 ``PromptRequest``，逐字节比较 ``build()`` /
   ``describe()`` 的输出。git 历史取不到旧版（浅克隆 / 非 git 环境）时该用例
   ``pytest.skip``，不误报。
"""

from __future__ import annotations

import functools
import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

from app.agent.assembly import sections as sections_mod
from app.agent.assembly.assembler import (
    BLOCKS,
    PromptAssembler,
    PromptRequest,
    describe,
    resolve_blocks,
)
from app.agent.assembly.builtin_sections import default_sections
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

DEFAULT_NAMES = [
    "persona",
    "behavior",
    "emoji_hint",
    "schedule",
    "proactive",
    "message_meta",
    "skills",
    "memory",
    "background",
    "group_log",
    "history",
    "user",
]

REPO_ROOT = Path(__file__).resolve().parents[3]
ASSEMBLER_REL = "app/agent/assembly/assembler.py"


def _runtime() -> types.SimpleNamespace:
    """无副作用的 runtime 替身：注册表跟着它走，但不需要真实 Agent 运行时。"""
    return types.SimpleNamespace(bot_id=778)


def _section(name: str, order: int, text: str = "", **kw) -> Section:
    def builder(req):  # noqa: ANN001, ANN202
        return [{"role": "system", "content": text}] if text else []

    return Section(name, order, builder, **kw)


# ==================== 1. 默认 12 段 ====================


def test_default_resolve_is_exactly_twelve_ordered_sections():
    registry = SectionRegistry()
    resolved = registry.resolve()

    assert [s.name for s in resolved] == DEFAULT_NAMES
    assert len(resolved) == 12
    assert [s.order for s in resolved] == sorted(s.order for s in resolved)
    assert all(s.scope == SCOPE_AGENT for s in resolved)
    # 旧硬编码块表（BLOCKS，含兼容 extra 通道）与新默认段同源：extra 插在 100 与 110 之间
    assert [b.name for b in BLOCKS] == DEFAULT_NAMES[:10] + ["extra"] + DEFAULT_NAMES[10:]
    assert [s.name for s in default_sections()] == DEFAULT_NAMES


def test_registry_of_hangs_registry_on_runtime_and_caches_it():
    runtime = _runtime()
    first = registry_of(runtime)
    second = registry_of(runtime)
    assert first is second
    assert runtime.prompt_sections is first
    assert [s.name for s in resolve_sections(runtime)] == DEFAULT_NAMES


def test_process_default_registry_is_isolated_from_bot_registries(monkeypatch):
    """没有 runtime 的直调用进程表；bot runtime 上注册的段不会漏进直调路径。"""
    monkeypatch.setattr(sections_mod, "_PROCESS_DEFAULT", None)
    runtime = _runtime()
    register_section(runtime, _section("bot_only", 55, "x"))

    assert "bot_only" in [s.name for s in resolve_sections(runtime)]
    assert "bot_only" not in [s.name for s in resolve_sections(None)]


def test_two_bot_runtimes_do_not_share_sections():
    a, b = _runtime(), _runtime()
    register_section(a, _section("only_a", 55, "A"))
    assert "only_a" in [s.name for s in resolve_sections(a)]
    assert "only_a" not in [s.name for s in resolve_sections(b)]


# ==================== 2. order=55 落在 proactive 与 message_meta 之间 ====================


def test_order_55_section_lands_between_proactive_and_message_meta():
    runtime = _runtime()
    register_section(runtime, _section("between", 55, "### 插进来的段"))

    names = [s.name for s in resolve_sections(runtime)]
    assert len(names) == 13
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
    names = [s.name for s in resolve_blocks(PromptRequest(runtime=runtime))]
    assert names == DEFAULT_NAMES[:10] + ["extra"] + DEFAULT_NAMES[10:]


def test_plugin_registered_extra_overrides_compat_channel():
    """插件注册同名 ``extra`` 段时以插件为准，不会插出两条。"""
    runtime = _runtime()
    register_section(runtime, _section("extra", 105, "插件版额外块"))
    names = [s.name for s in resolve_blocks(PromptRequest(runtime=runtime))]
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
    assert [s.name for s in globals_only if s.name == "demo"] == ["demo"]
    assert globals_only[[s.name for s in globals_only].index("demo")].scope == SCOPE_GLOBAL


def test_agent_scope_shadowing_also_works_through_runtime_registry():
    runtime = _runtime()
    register_section(runtime, _section("demo", 35, "GLOBAL 版", scope=SCOPE_GLOBAL))
    assert [s.name for s in resolve_sections(runtime)].count("demo") == 1

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


def test_unregister_returns_to_default_sections():
    runtime = _runtime()
    assert len(resolve_sections(runtime)) == 12

    register_section(runtime, _section("temp", 55, "临时段"))
    assert len(resolve_sections(runtime)) == 13
    assert "temp" in [s.name for s in resolve_sections(runtime)]

    assert unregister_section(runtime, "temp") == 1
    resolved = resolve_sections(runtime)
    assert len(resolved) == 12
    assert [s.name for s in resolved] == DEFAULT_NAMES

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
    assert [s.name for s in resolve_sections(runtime)] == DEFAULT_NAMES


# ==================== 6. 与改造前实现逐字节一致 ====================


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",   # git 输出是 UTF-8；Windows 默认 GBK 会让中文源码解码失败
        errors="replace",
        timeout=60,
        check=False,
    )


@functools.lru_cache(maxsize=1)
def _legacy_assembler_source() -> str | None:
    """从 git 历史里找改造前的 assembler.py（不含 SectionRegistry 的最近版本）。"""
    revs = _git("log", "--format=%H", "-n", "50", "--", ASSEMBLER_REL)
    if revs.returncode != 0:
        return None
    for rev in revs.stdout.split():
        shown = _git("show", f"{rev}:{ASSEMBLER_REL}")
        if shown.returncode != 0:
            continue
        source = shown.stdout
        if "SectionRegistry" in source:
            continue
        if "BLOCKS: tuple[Block, ...]" in source and "def build_all_blocks" in source:
            return source
    return None


def _load_legacy_module(tmp_path_factory) -> types.ModuleType:
    source = _legacy_assembler_source()
    if not source:
        pytest.skip("git 历史里找不到改造前的 assembler.py（浅克隆/非 git 环境）")
    path = Path(tmp_path_factory.mktemp("q4_legacy")) / "legacy_assembler.py"
    path.write_text(source, encoding="utf-8")
    module_name = "_q4_legacy_assembler"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module  # dataclass 需要按模块名解析
    try:
        spec.loader.exec_module(module)
    except Exception:  # noqa: BLE001 —— 旧文件 import 失败不能把测试变成假绿
        sys.modules.pop(module_name, None)
        raise
    return module


@pytest.fixture(scope="module")
def legacy_assembler(tmp_path_factory):
    return _load_legacy_module(tmp_path_factory)


ALL_TOOLS = {
    "set_msg_emoji_like",
    "get_chat_history",
    "send_poke",
    "expand_recent",
    "expand_message",
    "expand_user",
    "get_current_session",
}


def _request_kwargs(runtime) -> dict:
    """代表性请求：人格 / 行为 / 表情 / 技能 / 记忆 / 背景 / 群记录 / 额外块 / 历史 / 本轮。"""
    return {
        "runtime": runtime,
        "ctx": types.SimpleNamespace(state={"message_meta_injected": True}),
        "config": {
            "system_prompt": "你是助手",
            "assembly_profile": "qq_agent",
            "behavior_rules_enable": True,
            "meta_instruction_mode": "legacy",
            "user_message_format": "legacy",
            "history_background_enable": True,
            "outbound_directive_enable": True,
            "emoji_react_level": 1,
        },
        "session_id": "group_778",
        "is_private": False,
        "user_id": 10001,
        "group_id": 778,
        "kind": "chat",
        "user_text": "现在的消息",
        "raw_user_text": "现在的消息",
        "memory_source_text": "现在的消息",
        "history": [
            {"role": "user", "content": "上一句", "nickname": "小明"},
            {"role": "assistant", "content": "上一答"},
        ],
        "pre_history_text": "### 当前聊天环境\n群里在聊学费",
        "referent_text": "### 当前对话焦点\n刚才在说星野",
        "skill_blocks": ["技能A", "技能B"],
        "memory_text": "### 长期记忆\n- 喜欢美式",
        "group_log_text": "【群聊环境记录】\n14:03 三哥(30003): 绝了",
        "extra_blocks": [("kb", "### 知识库\n- 学费 5000"), ("empty", "   ")],
        "available_tools": set(ALL_TOOLS),
        "modalities": ["text"],
        "schedule_enable": True,
        "extra_user_content_parts": ["<system_reminder>注意</system_reminder>"],
    }


def _cases(runtime) -> dict[str, tuple[dict, dict]]:
    """名字 → (旧实现 kwargs, 新实现 kwargs)：两边用同一份字段值，只有类不同。"""
    full = _request_kwargs(runtime)
    single = dict(full, config=dict(full["config"], system_prompt_style="single"))
    speech = dict(full, config=dict(full["config"], speech_via_tool=True))
    minimal = {
        "runtime": runtime,
        "config": {"system_prompt": "你是助手", "outbound_directive_enable": False},
        "session_id": "group_1",
        "user_text": "现在的消息",
        "raw_user_text": "现在的消息",
        "history": [{"role": "user", "content": "上一句"}],
        "available_tools": set(),
    }
    return {
        "full": (full, dict(full)),
        "single_style": (single, dict(single)),
        "speech": (speech, dict(speech)),
        "minimal": (minimal, dict(minimal)),
    }


def test_migrated_blocks_are_byte_identical_to_pre_refactor(legacy_assembler):
    """改造前 assembler（从 git 历史 import）与当前实现的 build/describe 逐字节一致。"""
    runtime = _runtime()
    old_class = legacy_assembler.PromptRequest
    for name, (old_kwargs, new_kwargs) in _cases(runtime).items():
        old_req = old_class(**old_kwargs)
        new_req = PromptRequest(**new_kwargs)

        old_messages = legacy_assembler.PromptAssembler().build(old_req)
        new_messages = PromptAssembler().build(new_req)
        # sort_keys + ensure_ascii=False：键顺序/中文字符都进入比较，等价于字节级
        old_bytes = json.dumps(old_messages, ensure_ascii=False, sort_keys=True)
        new_bytes = json.dumps(new_messages, ensure_ascii=False, sort_keys=True)
        assert new_bytes == old_bytes, f"case={name} messages 与改造前不一致"

        assert describe(new_req) == legacy_assembler.describe(old_req), f"case={name} describe 不一致"
