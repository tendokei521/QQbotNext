"""H5：提示词语义、背景块开关、以及"查看过就留在历史里"的端到端验收。"""

from __future__ import annotations

from app.llm import history_enrich
from app.llm.assembly import PromptRequest, build_background
from app.llm.prompt import (
    EXPANDED_MARKER_NOTE,
    LEGACY_MESSAGE_META_INSTRUCTION,
    MESSAGE_META_INSTRUCTION,
)
from app.llm.config import DEFAULT_LLM_CONFIG
from app.llm.config_schema import SCHEMA
from app.llm.group_context import format_history_for_llm


# ---------- 提示词：已展开标记的语义 ----------


def test_meta_instructions_explain_expanded_markers():
    for text in (LEGACY_MESSAGE_META_INSTRUCTION, MESSAGE_META_INSTRUCTION):
        assert "已展开" in text
        assert "expand_image" in text
        assert EXPANDED_MARKER_NOTE.strip().splitlines()[-1] in text


# ---------- 背景块开关 ----------


def _req(config: dict) -> PromptRequest:
    return PromptRequest(
        config=config,
        session_id="group_1",
        user_text="你好",
        referent_text="### 当前对话焦点",
        pre_history_text="### 当前聊天环境",
        schedule_enable=False,
    )


def test_background_includes_pre_history_by_default():
    blocks = build_background(_req({"system_prompt": "s", "outbound_directive_enable": False}))
    assert len(blocks) == 1
    assert "当前对话焦点" in blocks[0]["content"]
    assert "当前聊天环境" in blocks[0]["content"]


def test_background_can_drop_pre_history_but_keeps_referent():
    blocks = build_background(_req({
        "system_prompt": "s",
        "history_background_enable": False,
        "outbound_directive_enable": False,
    }))
    assert blocks[0]["content"] == "### 当前对话焦点"


def test_background_empty_when_both_parts_missing():
    req = _req({"system_prompt": "s"})
    req.referent_text = ""
    req.pre_history_text = ""
    assert build_background(req) == []


def test_config_exposes_background_switch():
    assert DEFAULT_LLM_CONFIG["history_background_enable"] is True
    assert "history_background_enable" in SCHEMA


# ---------- 端到端：第 1 轮查看 → 第 2 轮历史里仍是内容 ----------


def test_viewed_content_stays_in_history_across_turns(tmp_path, monkeypatch):
    """核心验收：第 1 轮展开的引用，第 2 轮渲染历史时不再是占位。"""
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    monkeypatch.setattr(history_enrich, "_STORE", None, raising=False)
    history_enrich._LEDGERS.clear()

    entry = {
        "role": "user",
        "message_id": "1001",
        "time": 1788342260,
        "base": {
            "time": 1788342260, "sender_id": "20002", "sender_name": "小明",
            "text": "[引用456] 这是怎么回事",
            "segments": [{"type": "reply", "data": {"id": "456"}}],
        },
    }

    # 第 1 轮：模型调用 expand_message，工具记账
    ledger = history_enrich.ledger_for(778, "group_1")
    ledger.record("reply", "456", "上一条说的是学费", content="完整内容：学费怎么交", source="expand_message")
    assert history_enrich.commit(778, "group_1", trigger_message_id="1001") == 1

    # 第 2 轮：渲染历史 —— 占位被替换成内容（且不再重复附加）
    rendered = format_history_for_llm([entry], bot_id=778, session_id="group_1")
    assert "【已展开:引用456 → 上一条说的是学费】" in rendered[0]["content"]
    assert "[引用456]" not in rendered[0]["content"]
    assert len(rendered) == 1

    # 另一种情形：工具按 message_id 取回了"某条消息"，它没有占位可替换
    # → 作为附加块排在该条历史之后（不混进用户正文，避免伪造发言）
    ledger2 = history_enrich.ledger_for(778, "group_1")
    ledger2.record("message", "1001", "摘要", content="完整内容：学费怎么交", source="expand_recent")
    assert history_enrich.commit(778, "group_1", trigger_message_id="1001") == 1

    rendered = format_history_for_llm([entry], bot_id=778, session_id="group_1")
    assert rendered[-1] == {"role": "user", "content": "【已取回:消息1001】完整内容：学费怎么交"}
