"""五层装配模型的**不变量**（架构闸门）。

一次请求固定按 5 层拼：L1 系统提示词 → L2 人格预设 → L3 钩子注入 → L4 历史 → L5 本轮消息。
这里用源码 + 装配结果双重锁住三条容易被"顺手改坏"的约定：

1. **层序固定**：L1 必须在 L2 之前（规则压住人设里手写的输出格式条款），
   L2 在 L3 之前；层名/中文标签只有一份来源（``assembly.layers``）；
2. **L1 只产出一条 system**：层内多条（行为段 / 定时协议 / 主动性 / 消息格式说明）
   会被合并成一条，装配结果里 L1 恰好一条 system，且排在人设之前；
3. **内置段都登记了层**：新增内置段如果忘了登记，会被默认塞进 L3，
   提示词顺序就悄悄错了——这里用白名单把"忘登记"变成测试失败。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent.assembly.assembler import PromptAssembler, PromptRequest, build_all_blocks
from app.agent.assembly.layers import (
    DEFAULT_LAYER,
    LAYER_HISTORY,
    LAYER_INJECTED,
    LAYER_LABELS,
    LAYER_PERSONA,
    LAYER_SYSTEM_PROMPT,
    LAYER_USER,
    LAYERS,
    SECTION_LAYERS,
    layer_index,
    layer_of,
    normalize_layer,
    summarize_layers,
)
from app.agent.assembly.sections import Section, resolve_sections

#: 允许出现在 L1/L2 之外的段（层内顺序由段 order 决定）
REQUIRED_SECTION_LAYERS = {
    "behavior": LAYER_SYSTEM_PROMPT,
    "schedule": LAYER_SYSTEM_PROMPT,
    "proactive": LAYER_SYSTEM_PROMPT,
    "message_meta": LAYER_SYSTEM_PROMPT,
    "persona": LAYER_PERSONA,
    "emoji_hint": LAYER_INJECTED,
    "skills": LAYER_INJECTED,
    "memory": LAYER_INJECTED,
    "background": LAYER_INJECTED,
    "group_log": LAYER_INJECTED,
    "extra": LAYER_INJECTED,
    "history": LAYER_HISTORY,
    "user": LAYER_USER,
}


def _req(**kw) -> PromptRequest:
    kw.setdefault("config", {"system_prompt": "人设", "prompt_preset": "chat"})
    kw.setdefault("user_text", "hi")
    kw.setdefault("available_tools", {"send_message", "schedule_task", "get_chat_history"})
    return PromptRequest(**kw)


# ==================== 1. 层序 ====================


def test_layer_order_is_fixed():
    assert LAYERS == (LAYER_SYSTEM_PROMPT, LAYER_PERSONA, LAYER_INJECTED, LAYER_HISTORY, LAYER_USER)
    assert [layer_index(layer) for layer in LAYERS] == [0, 1, 2, 3, 4]
    assert layer_index(LAYER_SYSTEM_PROMPT) < layer_index(LAYER_PERSONA) < layer_index(LAYER_INJECTED)


def test_every_layer_has_a_label():
    for layer in LAYERS:
        assert LAYER_LABELS.get(layer), f"{layer} 缺中文标签"


def test_unknown_layer_falls_back_to_injected():
    """拼错层名只该"归到 L3"，不该把段丢掉（少一段信息比顺序错更不能接受）。"""
    assert normalize_layer("nonsense") == DEFAULT_LAYER == LAYER_INJECTED
    assert normalize_layer("") == LAYER_INJECTED


# ==================== 2. 内置段的层登记 ====================


def test_builtin_sections_are_all_registered():
    """每个默认段的层都必须登记在本表里（新增段忘登记 → 本测试失败）。"""
    for section in resolve_sections(None):
        assert section.name in REQUIRED_SECTION_LAYERS, (
            f"内置段 {section.name} 没登记层；请写进 assembly.layers.SECTION_LAYERS"
        )
        assert layer_of(section) == REQUIRED_SECTION_LAYERS[section.name]


def test_plugin_section_defaults_to_injected_layer():
    """插件段默认进 L3（"我现在知道什么"），想进 L1 必须显式声明 layer。"""
    assert layer_of(Section("my_block", 500, lambda req: [])) == LAYER_INJECTED
    assert layer_of(Section("my_block", 500, lambda req: [], layer="system_prompt")) == LAYER_SYSTEM_PROMPT


def test_resolved_sections_follow_layer_order():
    layers = [layer_of(section) for section in resolve_sections(None)]
    indexes = [layer_index(layer) for layer in layers]
    assert indexes == sorted(indexes), f"段没有按层排好: {list(zip([s.name for s in resolve_sections(None)], layers))}"


# ==================== 3. 装配结果 ====================


def test_system_prompt_layer_merges_into_exactly_one_system():
    """L1 层内多条（行为/定时/主动性/格式说明）合并成一条 system。"""
    req = _req()
    built = build_all_blocks(req)
    l1_blocks = [(block, messages) for block, messages in built if layer_of(block) == LAYER_SYSTEM_PROMPT]
    non_empty = [(block, messages) for block, messages in l1_blocks if messages]
    assert non_empty, "L1 应有内容"
    systems = [msg for _block, messages in l1_blocks for msg in messages if msg.get("role") == "system"]
    assert len(systems) == 1, f"L1 合并后应只剩一条 system，实际 {len(systems)}"
    assert "【安全规则" in systems[0]["content"], "合并结果应是规则正文（行为段）"


def test_prompt_order_is_l1_then_persona():
    messages = PromptAssembler().build(_req())
    systems = [str(m.get("content") or "") for m in messages if m.get("role") == "system"]
    assert "【安全规则" in systems[0], "第一条必须是 L1 规则段"
    assert systems[1] == "人设", "L2 人格紧跟 L1"


def test_summarize_layers_lists_non_empty_layers_only():
    rows = [
        {"block": "behavior", "layer": LAYER_SYSTEM_PROMPT, "role": "system", "chars": 100},
        {"block": "persona", "layer": LAYER_PERSONA, "role": "system", "chars": 10},
        {"block": "memory", "layer": LAYER_INJECTED, "role": "-", "chars": 0},
        {"block": "user", "layer": LAYER_USER, "role": "user", "chars": 2},
    ]
    text = summarize_layers(rows)
    assert "L1 系统提示词" in text and "behavior(100)" in text
    assert "L2 人格预设" in text and "persona(10)" in text
    assert "L3 钩子注入" not in text, "空段不该出现在摘要里"
    assert "memory" not in text


def test_debug_layers_snapshot_goes_to_ctx():
    """装配时把层摘要挂到 ctx，供 ``[Prompt]`` 日志打头（排障先看层）。"""
    ctx = SimpleNamespace(state={})
    PromptAssembler().build(_req(ctx=ctx))
    assert ctx.state["assembly_layers"].startswith("L1 系统提示词")
    assert any(row["layer"] == LAYER_PERSONA for row in ctx.state["assembly_blocks"])
