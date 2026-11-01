"""`llm_prompt` 模块（精简版，8 键）契约。

验证要点：
1. 键集 = schema = default_config（8 键，两功能：提示词注入 / 消息组装方式）；
2. 模块配置真的影响装配（行为规则覆盖）与消息渲染（格式 / 发送者 / 引用档位）；
3. 模块配置优先于 agent 配置（ConfigView 叠链）；
4. 仍声明 assembly 能力（装配槽契约不变）。
"""

from __future__ import annotations

import types

from app.agent.assembly.assembler import PromptAssembler, PromptRequest
from app.agent.delivery.output import ConfigView
from app.agent.loop.context import LlmContext
from app.domain.events import GroupMessageEvent, MessageSegment, UserInfo
from app.plugins.llm_prompt.main import OWNED_KEYS, Module
from app.plugins.llm_prompt.src.assembly import AssemblyAdapter
from app.plugins.llm_prompt.src.meta import collect_user_context, format_user_context


def test_owned_keys_are_exactly_the_schema():
    assert len(OWNED_KEYS) == 8
    assert set(OWNED_KEYS) == set(Module.default_config) == set(Module.config_schema)
    # 两功能各占一半：提示词注入 2 + 消息组装 6
    assert {"behavior_rules_override", "participation"} <= set(OWNED_KEYS)
    assert {
        "user_message_format",
        "prompt_time_position",
        "sender_label",
        "quote_and_at",
        "include_sent",
        "auto_disambiguation",
    } <= set(OWNED_KEYS)


def test_module_declares_assembly_capability():
    assert "assembly" in Module.supersedes
    assert Module.category == "LLM"


def _req(config=None, **over):
    base = dict(
        config=config if config is not None else {},
        session_id="group_1",
        is_private=False,
        user_text="本轮消息",
        history=[{"role": "user", "content": "上一句"}],
    )
    base.update(over)
    return PromptRequest(**base)


def _behavior_text(req) -> str:
    for msg in PromptAssembler().build(req):
        text = str(msg.get("content") or "")
        if msg.get("role") == "system" and "【安全规则" in text:
            return text
    raise AssertionError("没找到行为规则段")


def test_module_config_drives_behavior_block():
    """`behavior_rules_override` 由本模块提供时整段替换行为规则。"""
    agent_cfg = {"system_prompt": "你是助手", "prompt_preset": "chat"}
    module_cfg = {"behavior_rules_override": "自定义行为规则"}

    adapter = AssemblyAdapter(SimpleConfig(module_cfg), OWNED_KEYS)
    plain = _behavior_text(_req(config=dict(agent_cfg)))
    overridden = _behavior_text(_req(config=adapter.view_for(dict(agent_cfg))))

    assert "自定义行为规则" in overridden
    assert "自定义行为规则" not in plain
    # 安全规则与工作方式仍强制追加（底线不被覆盖掉）
    assert "【安全规则" in overridden and "【工作方式】" in overridden


def test_module_config_wins_over_agent_config():
    view = ConfigView(
        {"user_message_format": "single_line", "sender_label": "compact"},
        SimpleConfig({"user_message_format": "multi_line"}),
        OWNED_KEYS,
    )
    assert view.get("user_message_format") == "multi_line"
    # 不归本模块的键回退 agent 配置
    assert view.get("prompt_preset") is None


# ---------- 消息渲染真的受模块配置驱动 ----------


class SimpleConfig:
    def __init__(self, data: dict):
        self.data = data

    def get(self, key, default=None):
        return self.data.get(key, default)


class _FakeBot:
    def __init__(self):
        self.calls = 0

    async def get_msg(self, message_id):
        self.calls += 1
        return {
            "data": {
                "sender": {"card": "小红", "user_id": 10002},
                "message": [{"type": "text", "data": {"text": "昨晚那部剧"}}],
            }
        }


def _event(bot=None):
    return GroupMessageEvent(
        event_type="message_group",
        message_type="group",
        time=1730000000,
        user_id=20002,
        self_id=3437542570,
        message=[
            MessageSegment("reply", {"id": "456"}),
            MessageSegment("text", {"text": "刚那条是什么意思？"}),
        ],
        user=UserInfo(user_id=20002, nickname="小明"),
        group=types.SimpleNamespace(group_id=466052056),
        bot=bot,
    )


async def _render(config: dict, bot=None):
    runtime = types.SimpleNamespace(config=config, bot_id=3437542570)
    ctx = LlmContext(event=_event(bot), runtime=runtime, bot=bot, session_id="group_1")
    ctx.user_text = "刚那条是什么意思？"
    await collect_user_context(ctx)
    await format_user_context(ctx)
    return ctx


async def test_module_config_drives_rendering():
    compact = await _render({"user_message_format": "single_line"}, _FakeBot())
    assert "小明(20002): 刚那条是什么意思？" in compact.user_text

    multi = await _render({"user_message_format": "multi_line"}, _FakeBot())
    assert "发送者：小明(20002)" in multi.user_text
    assert "发送了：刚那条是什么意思？" in multi.user_text


async def test_quote_mode_controls_fetching():
    bot_mark = _FakeBot()
    mark = await _render({"quote_and_at": "mark"}, bot_mark)
    assert bot_mark.calls == 0 and "引用了：" in mark.user_text

    bot_enrich = _FakeBot()
    enrich = await _render({"quote_and_at": "enrich"}, bot_enrich)
    assert bot_enrich.calls == 1 and "昨晚那部剧" in enrich.user_text
