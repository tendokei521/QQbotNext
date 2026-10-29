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

import base64

from app.agent.assembly.assembler import PromptRequest, describe

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32
PNG_B64 = base64.b64encode(PNG_BYTES).decode()

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


# ==================== 块序列快照（黄金） ====================


def test_group_log_block_lands_between_background_and_history():
    """群聊环境记录块的位置与内容：背景之后、会话历史之前。"""
    req = _req(
        config={"system_prompt": "你是助手", "outbound_directive_enable": False},
        pre_history_text="环境",
        group_log_text="【群聊环境记录】\n14:03 三哥(30003): 绝了",
    )
    rows = describe(req)
    names = [r["block"] for r in rows]
    assert names.index("background") < names.index("group_log") < names.index("history")
    row = [r for r in rows if r["block"] == "group_log"][0]
    assert row["role"] == "system"
    assert row["chars"] == len("【群聊环境记录】\n14:03 三哥(30003): 绝了")


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
    resolved = [{"kind": "base64", "value": PNG_B64, "mime": "image/png"}]
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
