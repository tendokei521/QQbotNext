"""``system_prompt_style``：把多块 system 折成一整条。

框架默认按「块」产出 system 消息（人设 / 定时协议 / 主动性 / 格式说明 / 技能 ×N /
记忆 / 背景 / 群聊记录），而 AstrBot 之类第三方框架的组装结果是**一整条** system
prompt（``req.system_prompt`` 就地拼接）。要 1:1 对比迁移，得能把两种形态对齐。

本文件锁住：
1. 默认 ``blocks`` 形态**逐字不变**（既有黄金快照测试的安全前提）；
2. ``single`` 折叠只动 system 消息：文本顺序保留、``\\n\\n`` 连接、空内容丢弃；
3. 折叠后 ``describe()`` 的「块 ↔ 消息」对位仍然成立（被折走的块显示为无消息）。
"""

from __future__ import annotations

from app.llm.assembly import (
    SYSTEM_STYLE_BLOCKS,
    SYSTEM_STYLE_SINGLE,
    PromptAssembler,
    PromptRequest,
    describe,
    fold_system_blocks,
    system_style,
)

BASE_CONFIG = {"system_prompt": "你是助手", "outbound_directive_enable": False}


def _req(**kw) -> PromptRequest:
    """多 system 块请求：人设 + 定时协议 + 格式说明 + 技能 ×2 + 记忆 + 背景 + 历史 + 本轮。"""
    base = dict(
        config=dict(BASE_CONFIG),
        session_id="group_1",
        user_text="你好",
        raw_user_text="你好",
        history=[{"role": "user", "content": "上一句"}, {"role": "assistant", "content": "上一答"}],
        available_tools=set(),
        schedule_enable=True,
        skill_blocks=["技能块A", "技能块B"],
        memory_text="记忆块",
        pre_history_text="背景块",
    )
    base.update(kw)
    return PromptRequest(**base)


def _single_req(**kw) -> PromptRequest:
    kw.setdefault("config", {**BASE_CONFIG, "system_prompt_style": SYSTEM_STYLE_SINGLE})
    return _req(**kw)


def _systems(messages: list[dict]) -> list[str]:
    return [m["content"] for m in messages if m.get("role") == "system"]


# ==================== 默认形态不变 ====================


def test_default_style_is_blocks():
    assert system_style(_req()) == SYSTEM_STYLE_BLOCKS
    assert len(_systems(PromptAssembler().build(_req()))) > 1, "默认仍是多块 system"


def test_unknown_style_falls_back_to_blocks():
    """未知/空值一律按默认处理，避免写错一个字符串就把 prompt 折叠成怪形态。"""
    assert system_style(_req(config={**BASE_CONFIG, "system_prompt_style": "single-ish"})) == SYSTEM_STYLE_BLOCKS
    assert system_style(_req(config={**BASE_CONFIG, "system_prompt_style": ""})) == SYSTEM_STYLE_BLOCKS
    assert system_style(_req(config={**BASE_CONFIG, "system_prompt_style": None})) == SYSTEM_STYLE_BLOCKS


# ==================== single 折叠 ====================


def test_single_style_folds_all_system_into_one():
    blocks_messages = PromptAssembler().build(_req())
    folded = PromptAssembler().build(_single_req())

    before = _systems(blocks_messages)
    assert len(before) > 1

    after = _systems(folded)
    assert len(after) == 1, "single 形态下只应剩一条 system"
    assert after[0] == "\n\n".join(before), "折叠文本 = 各块文本按序 \\n\\n 连接"
    assert folded[0]["role"] == "system", "折叠结果仍在最前"


def test_single_style_keeps_non_system_messages_untouched():
    """历史与本轮消息逐字不变，只动 system。"""
    blocks_messages = PromptAssembler().build(_req())
    folded = PromptAssembler().build(_single_req())

    stripped_before = [m for m in blocks_messages if m.get("role") != "system"]
    stripped_after = [m for m in folded if m.get("role") != "system"]
    assert stripped_after == stripped_before
    assert [m["role"] for m in stripped_after] == ["user", "assistant", "user"]


def test_single_style_drops_empty_system_text():
    """空 system（例如 system_prompt 为空）不产生多余分隔符。"""
    req = _single_req(config={**BASE_CONFIG, "system_prompt_style": SYSTEM_STYLE_SINGLE, "system_prompt": ""})
    content = _systems(PromptAssembler().build(req))[0]
    assert not content.startswith("\n")
    assert "\n\n\n" not in content


def test_fold_system_blocks_noop_without_system():
    """没有任何 system 块时原样返回（例如自定义块表只产出历史）。"""
    from app.llm.assembly import Block

    built = [(Block("history", lambda req: [{"role": "user", "content": "x"}]), [{"role": "user", "content": "x"}])]
    assert fold_system_blocks(built) == built


def test_fold_system_blocks_keeps_multimodal_content():
    """content 不是字符串的消息（多模态块）不参与折叠，避免被拼成字符串。"""
    from app.llm.assembly import Block

    media = {"role": "system", "content": [{"type": "text", "text": "x"}]}
    built = [(Block("persona", lambda req: [{"role": "system", "content": "文本"}]), [{"role": "system", "content": "文本"}]), (Block("m", lambda req: [media]), [media])]
    folded = fold_system_blocks(built)
    assert folded[0][1] == [{"role": "system", "content": "文本"}]  # 单独保留
    assert folded[1][1] == [media]


# ==================== describe 对位 ====================


def test_single_style_describe_stays_aligned_with_build():
    """``describe()`` 每块一行（空块也留行），非空行必须与最终消息逐条对位。"""
    req = _single_req()
    rows = describe(req)
    messages = PromptAssembler().build(req)

    non_empty = [r for r in rows if r["role"] != "-"]
    assert len(non_empty) == len(messages), "非空块行数应等于最终消息数（对位不串）"
    assert [r["block"] for r in non_empty] == ["persona", "history", "history", "user"]
    assert [r["role"] for r in non_empty] == ["system", messages[1]["role"], messages[2]["role"], "user"]
    assert non_empty[0]["chars"] == len(messages[0]["content"])
    assert len([r for r in rows if r["role"] == "system"]) == 1
    assert [r for r in rows if r["role"] == "-"], "被折走消息的块应显示为无消息（一眼看出它去哪了）"
