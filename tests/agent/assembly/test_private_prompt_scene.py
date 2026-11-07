"""私聊提示词的场景契约：不注入群聊人设 / 参与度 / 群触发规则。

线上事故（2026-11-07）：私聊请求里出现了「【群聊参与度】所有群消息都会进入你的
上下文」和「你是一个混在 QQ 群里的普通群友」，模型因此在私聊里回答
「这边好像只能看到群里的消息」。根因是提示词各段只有群聊版本。

这里锁住私聊版：
1. reply_rules：私聊规则，不提参与度/@；
2. persona 兜底：私聊不是"群友"；
3. behavior：不出现「混在 QQ 群里的普通群友」「群里的消息你都能看到」；
4. message_meta：私聊版约定（没有"当前群聊"、没有群消息凭证）。
"""

from __future__ import annotations

from app.agent.assembly.assembler import PromptRequest
from app.agent.prompts.convention import message_meta_section
from app.agent.prompts.participation import reply_rules_section
from app.agent.prompts.sections import build_behavior, build_persona


def _req(*, is_private: bool, config: dict | None = None, tools: set[str] | None = None) -> PromptRequest:
    base = {"prompt_preset": "chat", "reply_mode": "participation", "participation_level": "low"}
    base.update(config or {})
    return PromptRequest(
        config=base,
        is_private=is_private,
        available_tools=tools if tools is not None else {"get_context"},
        modalities=None,
        user_text="在吗",
    )


def _behavior_text(req: PromptRequest) -> str:
    blocks = build_behavior(req)
    assert blocks, "行为段不应为空"
    return "\n".join(str(block.get("content", "")) for block in blocks)


def test_private_reply_rules_do_not_mention_participation_or_at():
    text = reply_rules_section(_req(is_private=True))[0]["content"]
    assert "私聊" in text
    assert "【群聊参与度】" not in text
    assert "群消息" not in text
    # 私聊是普通输出：明确告诉模型正文就是回复
    assert "正文会直接作为消息发出" in text


def test_group_reply_rules_keep_participation_text():
    text = reply_rules_section(_req(is_private=False))[0]["content"]
    assert "群聊参与度" in text


def test_private_persona_fallback_is_not_group_member():
    req = _req(is_private=True, config={"system_prompt": ""})
    content = build_persona(req)[0]["content"]
    assert "群友" not in content
    assert "一对一" in content


def test_private_behavior_has_no_group_identity_or_participation_line():
    text = _behavior_text(_req(is_private=True))
    assert "混在 QQ 群里的普通群友" not in text
    assert "群里的消息你都能看到" not in text
    assert "群聊不是客服队列" not in text
    assert "一对一私聊" in text
    assert "对方直接找你" in text


def test_group_behavior_keeps_group_identity():
    text = _behavior_text(_req(is_private=False))
    assert "混在 QQ 群里的普通群友" in text


def test_private_message_meta_uses_private_convention():
    text = message_meta_section(_req(is_private=True))[0]["content"]
    assert "当前群聊" not in text
    assert "auth_message_id" not in text
    assert "私聊里没有群聊" in text
