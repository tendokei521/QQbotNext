"""框架提示词的唯一位置契约（2026-11-03）。

1. 框架提示词模块都在 `app/agent/prompts/`，旧位置不再存在；
2. 框架注入点必须从该包 import（防止文案又散回业务代码）；
3. 提示词方案只剩两套（chat / roleplay），`assistant` 已删除。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_old_prompt_locations_are_gone():
    old = [
        "app/agent/assembly/presets.py",
        "app/agent/assembly/builtin_sections.py",
        "app/agent/message_assembly/convention.py",
    ]
    assert not [p for p in old if (ROOT / p).exists()], f"旧提示词位置仍存在: {old}"


def test_framework_injection_sites_import_from_prompts():
    sites = [
        "app/agent/assembly/assembler.py",
        "app/agent/group_log/render.py",
        "app/agent/assembly/compress.py",
        "app/agent/assembly/compaction.py",
        "app/agent/loop/prompt_prep.py",
        "app/agent/loop/tool_loop.py",
        "app/agent/tools/spec.py",
    ]
    misses = [rel for rel in sites if "app.agent.prompts" not in (ROOT / rel).read_text(encoding="utf-8")]
    assert not misses, f"这些注入点没有从 app.agent.prompts 取文案: {misses}"


def test_prompt_presets_are_only_chat_and_roleplay():
    from app.agent.prompts import presets

    assert [p.key for p in presets.PRESETS] == ["chat", "roleplay"]
    assert presets.resolve({"prompt_preset": "assistant"}) is presets.CHAT, (
        "已删除的 assistant 方案必须回落默认 chat"
    )
