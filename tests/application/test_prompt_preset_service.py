"""系统提示词方案的可编辑覆盖（设置页「编辑系统提示词」）。

锁三件事：
1. 三套方案都能读出默认文本；
2. 保存覆盖后装配优先用覆盖文本（账号级 behavior_rules_override 仍更优先）；
3. 覆盖是全局 kv，重启后仍在；空文本 = 恢复默认。
"""

from __future__ import annotations

import pytest

from app.agent.assembly import presets
from app.agent.assembly.assembler import PromptAssembler, PromptRequest
from app.application import prompt_preset_service


@pytest.fixture(autouse=True)
async def _clean(container, config_service):
    """每个用例前后都把缓存与 kv 清干净（模块级缓存是全局的）。"""
    await prompt_preset_service.load_overrides(config_service)
    for key in presets.keys():
        await prompt_preset_service.reset_override(config_service, key)
    yield
    for key in presets.keys():
        await prompt_preset_service.reset_override(config_service, key)


def test_describe_lists_three_presets_with_default_text():
    rows = prompt_preset_service.describe_presets()

    assert [row["key"] for row in rows] == ["chat", "roleplay", "assistant"]
    assert all(row["default_text"].strip() for row in rows), "三套都要有默认文本"
    assert all(row["customized"] is False for row in rows)
    assert rows[0]["effective_text"] == rows[0]["default_text"]


async def test_save_override_persists_and_wins_in_assembly(config_service):
    custom = "【自定义系统提示词】\n- 只按这套说话。"
    result = await prompt_preset_service.save_override(config_service, "chat", custom)

    assert result["customized"] is True
    assert prompt_preset_service.get_override("chat") == custom

    # 重启（重新从 kv 加载）后仍在
    await prompt_preset_service.load_overrides(config_service)
    assert prompt_preset_service.get_override("chat") == custom

    req = PromptRequest(
        config={"prompt_preset": "chat", "system_prompt": "人设"},
        user_text="hi",
        available_tools={"send_message"},
    )
    systems = [m["content"] for m in PromptAssembler().build(req) if m["role"] == "system"]
    assert custom in systems, "装配应直接使用覆盖文本"


async def test_account_level_override_still_wins(config_service):
    await prompt_preset_service.save_override(config_service, "chat", "全局自定义")

    req = PromptRequest(
        config={
            "prompt_preset": "chat",
            "behavior_rules_override": "账号级自定义",
            "system_prompt": "人设",
        },
        user_text="hi",
        available_tools={"send_message"},
    )
    systems = " ".join(str(m["content"]) for m in PromptAssembler().build(req) if m["role"] == "system")

    assert "账号级自定义" in systems
    assert "全局自定义" not in systems


async def test_reset_restores_default_and_empty_text_is_reset(config_service):
    await prompt_preset_service.save_override(config_service, "roleplay", "临时文本")
    await prompt_preset_service.reset_override(config_service, "roleplay")

    assert prompt_preset_service.get_override("roleplay") == ""
    row = next(r for r in prompt_preset_service.describe_presets() if r["key"] == "roleplay")
    assert row["customized"] is False

    # 空文本等同恢复默认
    await prompt_preset_service.save_override(config_service, "roleplay", "  ")
    assert prompt_preset_service.get_override("roleplay") == ""


async def test_unknown_key_and_oversize_are_rejected(config_service):
    with pytest.raises(prompt_preset_service.PromptPresetError):
        await prompt_preset_service.save_override(config_service, "nope", "x")

    with pytest.raises(prompt_preset_service.PromptPresetError):
        await prompt_preset_service.save_override(
            config_service, "chat", "x" * (prompt_preset_service.MAX_TEXT_CHARS + 1)
        )
