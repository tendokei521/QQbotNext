"""装配器等价性 + 块序列黄金快照（重构的安全网）。

背景：同一类请求此前在四个地方各自组装 messages（chat.generate_response /
chat.stream_response / scheduler._build_messages / proactive 内联），
块顺序只存在于各自函数体里。本文件锁住两件事：

1. **等价性**：``assembly.PromptRequest`` 走新装配器后，与旧 ``build_messages``
   在相同输入下产出**逐字相同**的 messages（重构期间不允许任何差异）；
2. **块序列**：``describe()`` 给出的 ``[{block, role, chars}]`` 快照，
   任何"顺手调整顺序"都会在这里显形。
"""

from __future__ import annotations

from app.llm.assembly import BLOCKS, PromptRequest, PromptAssembler, describe
from app.llm.prompt import (
    LEGACY_MESSAGE_META_INSTRUCTION,
    SCHEDULE_INSTRUCTION,
    build_messages as legacy_build_messages,
)

# ==================== 等价性 ====================


def _req(**kw) -> PromptRequest:
    base = dict(
        # outbound_directive_enable=False 让「主动性」块在无工具时确实为空——
        # 便于断言"没有可用能力就不注入"；开启时它默认会带一条引用指令。
        config={"system_prompt": "你是助手", "outbound_directive_enable": False},
        session_id="group_778",
        user_text="现在的消息",
        raw_user_text="现在的消息",
        history=[{"role": "user", "content": "上一句"}],
        available_tools=set(),
    )
    base.update(kw)
    return PromptRequest(**base)


def test_equivalent_to_legacy_minimal():
    """最小组合：人设 + 定时协议 + 格式说明 + 历史 + user。"""
    req = _req()
    new = PromptAssembler().build(req)
    old = legacy_build_messages(
        system_prompt="你是助手",
        pre_history_text="",
        history=[{"role": "user", "content": "上一句"}],
        user_text="现在的消息",
        with_schedule_instruction=True,
        skills=[],
        memory_text="",
        message_meta_instruction=LEGACY_MESSAGE_META_INSTRUCTION,
        proactive_instruction=None,
    )
    assert new == old


def test_equivalent_with_all_blocks_present():
    """全块在场：人设/定时/主动性/格式说明/技能/记忆/背景/历史/user，且顺序逐字一致。"""
    from app.llm.prompt import build_proactive_instruction

    cfg = {
        "system_prompt": "你是助手",
        "meta_sender_style": "legacy",
        "outbound_directive_enable": True,
    }
    tools = {"get_chat_history", "get_current_session", "send_poke"}
    proactive_text = build_proactive_instruction(cfg, "现在的消息", available_tools=tools)
    assert proactive_text  # 前置校验：这组能力确实会产出主动性块

    req = _req(
        config=cfg,
        available_tools=tools,
        pre_history_text="### 当前聊天环境\n群里在聊学费",
        referent_text="### 当前对话焦点\n刚才在说星野",
        skill_blocks=["技能A", "技能B"],
        memory_text="### 长期记忆\n- 喜欢美式",
    )
    new = PromptAssembler().build(req)
    old = legacy_build_messages(
        system_prompt="你是助手",
        pre_history_text="### 当前对话焦点\n刚才在说星野\n\n### 当前聊天环境\n群里在聊学费",
        history=[{"role": "user", "content": "上一句"}],
        user_text="现在的消息",
        with_schedule_instruction=True,
        skills=["技能A", "技能B"],
        memory_text="### 长期记忆\n- 喜欢美式",
        message_meta_instruction=LEGACY_MESSAGE_META_INSTRUCTION,
        proactive_instruction=proactive_text,
    )
    assert new == old
    assert [m["role"] for m in new] == [
        "system",  # persona
        "system",  # schedule
        "system",  # proactive
        "system",  # message_meta
        "system",  # skill A
        "system",  # skill B
        "system",  # memory
        "system",  # background（指代 + 环境合并）
        "user",    # history
        "user",    # user
    ]


def test_equivalent_without_schedule_and_with_off_meta():
    req = _req(
        config={
            "system_prompt": "你是助手",
            "meta_sender_style": "off",
            "outbound_directive_enable": False,
        },
        schedule_enable=False,
    )
    new = PromptAssembler().build(req)
    old = legacy_build_messages(
        system_prompt="你是助手",
        user_text="现在的消息",
        history=[{"role": "user", "content": "上一句"}],
        with_schedule_instruction=False,
        message_meta_instruction=None,
    )
    assert new == old


def test_components_are_independent_and_named():
    """块是独立函数：缺哪块就不出现哪块（不是靠 if 堆在装配体里）。"""
    req = _req(referent_text="焦点行", pre_history_text="环境行")
    req.history = []
    req.skill_blocks = []
    req.memory_text = ""
    messages = PromptAssembler().build(req)
    assert len(messages) == 5  # persona + schedule + message_meta + background + user
    assert messages[2]["content"] == LEGACY_MESSAGE_META_INSTRUCTION
    assert messages[3]["content"] == "焦点行\n\n环境行"
    assert messages[4]["content"] == "现在的消息"


# ==================== 块序列快照（黄金） ====================


def test_block_order_is_declared_and_stable():
    assert [b.name for b in BLOCKS] == [
        "persona",
        "schedule",
        "proactive",
        "message_meta",
        "skills",
        "memory",
        "background",
        "history",
        "user",
    ]


def test_describe_snapshot_typical_group_chat():
    """典型群聊：块序列 + 角色 + 长度。任何顺序变化都会在此显形。"""
    req = _req(
        config={"system_prompt": "你是助手", "outbound_directive_enable": False},
        pre_history_text="环境",
        referent_text="焦点",
    )
    rows = describe(req)
    assert rows == [
        {"block": "persona", "role": "system", "chars": 4, "messages": 1},
        {"block": "schedule", "role": "system", "chars": len(SCHEDULE_INSTRUCTION), "messages": 1},
        {"block": "proactive", "role": "-", "chars": 0, "messages": 0},
        {
            "block": "message_meta",
            "role": "system",
            "chars": len(LEGACY_MESSAGE_META_INSTRUCTION),
            "messages": 1,
        },
        {"block": "skills", "role": "-", "chars": 0, "messages": 0},
        {"block": "memory", "role": "-", "chars": 0, "messages": 0},
        {"block": "background", "role": "system", "chars": len("焦点\n\n环境"), "messages": 1},
        {"block": "history", "role": "user", "chars": len("上一句"), "messages": 1},
        {"block": "user", "role": "user", "chars": len("现在的消息"), "messages": 1},
    ]


def test_describe_reports_media_for_image_turn():
    """图片归位后 describe 报 user+media；纯文本模型不会出现 media。"""
    from app.domain.events import GroupMessageEvent, MessageSegment

    event = GroupMessageEvent(
        event_type="message_group",
        message_type="group",
        time=1,
        user_id=2,
        self_id=3,
        message=[MessageSegment("image", {"url": "https://a/1.png"})],
    )
    resolved = [{"kind": "url", "value": "https://a/1.png"}]
    req = PromptRequest(
        config={"system_prompt": "s", "outbound_directive_enable": False},
        event=event,
        session_id="group_1",
        user_text="[图片]",
        user_images=resolved,
        modalities=["text", "image"],
    )
    rows = describe(req)
    assert [r["block"] for r in rows][-1] == "user"
    assert rows[-1]["role"] == "user+media"

    # 文本模型：保持 [图片] 占位，不出现 media
    text_only = PromptRequest(
        config={"system_prompt": "s", "outbound_directive_enable": False},
        event=event,
        session_id="group_1",
        user_text="[图片]",
        user_images=resolved,
        modalities=["text"],
    )
    rows = describe(text_only)
    assert not any("media" in r["role"] for r in rows)
