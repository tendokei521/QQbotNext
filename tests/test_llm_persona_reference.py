"""角色预设「活引用」：`persona_preset_id` 每轮解析（含预设对话前插）。

此前 Agent 的角色预设是「应用一次」把提示词**拷贝**进 `system_prompt` —— 改预设不会
生效，得回页面重新应用一次。这里锁住活引用口径，并保证**不设 `persona_preset_id` 时
行为逐字不变**（老配置零改动）。

活引用的三个语义：
1. 每轮组装按 ID 读预设 → 改提示词/预设对话立刻生效；
2. 预设被删（悬挂引用）→ 留痕并回退 `system_prompt`，人设不会整块消失；
3. 读不到配置（服务缺失/异常）→ 同样回退，不抛异常。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.llm.assembly import (
    PromptAssembler,
    PromptRequest,
    build_history,
    build_persona,
    persona_begin_dialogs,
)

OLD_PROMPT = "旧人设"
PRESET = {
    "id": "p1",
    "name": "傲娇猫娘",
    "system_prompt": "你是傲娇猫娘。",
    "begin_dialogs": [
        {"role": "user", "content": "在吗"},
        {"role": "assistant", "content": "哼，在的。"},
    ],
}


class _ConfigService:
    def __init__(self, presets=None, boom=False):
        self.presets = dict(presets or {})
        self.boom = boom
        self.calls: list[str] = []

    def get_role_preset(self, role_id):
        self.calls.append(str(role_id))
        if self.boom:
            raise RuntimeError("配置中心炸了")
        return self.presets.get(str(role_id))


def _req(**kw):
    service = kw.pop("config_service", None)
    runtime = kw.pop("runtime", None)
    if runtime is None:
        runtime = SimpleNamespace(bot_id="bot_persona", config_service=service)
    config = kw.pop("config", None)
    base = dict(
        runtime=runtime,
        config=config if config is not None else {"system_prompt": OLD_PROMPT, "outbound_directive_enable": False},
        session_id="group_1",
        user_text="你好",
        raw_user_text="你好",
        history=[{"role": "user", "content": "上一句"}],
        available_tools=set(),
    )
    base.update(kw)
    return PromptRequest(**base)


def _system(messages: list[dict]) -> str:
    return messages[0]["content"]


# ==================== 默认行为不变 ====================


def test_without_preset_config_behaviour_is_unchanged():
    req = _req(config_service=_ConfigService({"p1": PRESET}))
    assert _system(PromptAssembler().build(req)) == OLD_PROMPT
    assert build_history(req) == [{"role": "user", "content": "上一句"}], "没引用预设就不该插入预设对话"
    assert persona_begin_dialogs(req) == []


# ==================== 活引用 ====================


def test_preset_prompt_is_used_when_referenced():
    req = _req(config_service=_ConfigService({"p1": PRESET}), config={"system_prompt": OLD_PROMPT, "persona_preset_id": "p1"})
    assert _system(PromptAssembler().build(req)) == "你是傲娇猫娘。"


def test_preset_edit_takes_effect_without_reapplying():
    """活引用：改预设内容后**下一次组装**立即反映，不需要重新"应用预设"。"""
    service = _ConfigService({"p1": dict(PRESET)})
    config = {"system_prompt": OLD_PROMPT, "persona_preset_id": "p1"}
    assert _system(PromptAssembler().build(_req(config_service=service, config=config))) == "你是傲娇猫娘。"

    service.presets["p1"]["system_prompt"] = "你是一只猫（改过了）。"
    assert _system(PromptAssembler().build(_req(config_service=service, config=config))) == "你是一只猫（改过了）。"


def test_begin_dialogs_prepended_to_history():
    req = _req(config_service=_ConfigService({"p1": PRESET}), config={"persona_preset_id": "p1"})
    messages = PromptAssembler().build(req)
    # 尾部四元组 = 预设对话 → 原历史 → 本轮（前面还有 persona/格式说明等 system 块，
    # 不该被这条用例绑死）
    assert [m["role"] for m in messages[-4:]] == ["user", "assistant", "user", "user"]
    assert messages[-4] == {"role": "user", "content": "在吗"}
    assert messages[-3] == {"role": "assistant", "content": "哼，在的。"}
    assert messages[-2] == {"role": "user", "content": "上一句"}, "预设对话在原历史之前"
    assert messages[-1] == {"role": "user", "content": "你好"}, "本轮消息仍在最后"


def test_begin_dialogs_are_normalized():
    preset = {"system_prompt": "人设", "begin_dialogs": [
        {"role": "USER", "content": "  你好  "},
        {"role": "system", "content": "角色不支持"},
        {"role": "user", "content": "   "},
        {"role": "assistant", "content": "在"},
        "不是字典",
        {"content": "缺 role"},
    ]}
    req = _req(config_service=_ConfigService({"p1": preset}), config={"persona_preset_id": "p1"})
    assert persona_begin_dialogs(req) == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "在"},
    ]


def test_begin_dialogs_bad_shapes_are_ignored():
    for bad in (None, "字符串", {"role": "user"}, [None, 42]):
        preset = {"system_prompt": "人设", "begin_dialogs": bad}
        req = _req(config_service=_ConfigService({"p1": preset}), config={"persona_preset_id": "p1"})
        assert persona_begin_dialogs(req) == []


# ==================== 悬挂引用 / 异常都回退 ====================


def test_missing_preset_falls_back_to_system_prompt():
    req = _req(config_service=_ConfigService({}), config={"system_prompt": OLD_PROMPT, "persona_preset_id": "gone"})
    assert _system(PromptAssembler().build(req)) == OLD_PROMPT
    assert build_history(req) == [{"role": "user", "content": "上一句"}]


def test_preset_without_prompt_falls_back():
    preset = {"id": "p1", "system_prompt": "   ", "begin_dialogs": [{"role": "user", "content": "hi"}]}
    req = _req(config_service=_ConfigService({"p1": preset}), config={"system_prompt": OLD_PROMPT, "persona_preset_id": "p1"})
    assert _system(PromptAssembler().build(req)) == OLD_PROMPT
    # 提示词为空但预设对话有效时，预设对话仍然前插（两者互不牵连）
    assert build_history(req)[0] == {"role": "user", "content": "hi"}


def test_config_service_exception_falls_back():
    req = _req(config_service=_ConfigService(boom=True), config={"system_prompt": OLD_PROMPT, "persona_preset_id": "p1"})
    assert _system(PromptAssembler().build(req)) == OLD_PROMPT


def test_runtime_without_config_service_is_safe():
    req = _req(runtime=SimpleNamespace(bot_id="bot_persona"), config={"system_prompt": OLD_PROMPT, "persona_preset_id": "p1"})
    assert build_persona(req) == [{"role": "system", "content": OLD_PROMPT}]
    assert persona_begin_dialogs(req) == []


# ==================== 与其它组装能力的配合 ====================


def test_single_style_folds_preset_prompt_into_one_system():
    req = _req(
        config_service=_ConfigService({"p1": PRESET}),
        config={"persona_preset_id": "p1", "system_prompt_style": "single", "outbound_directive_enable": False},
    )
    messages = PromptAssembler().build(req)
    systems = [m for m in messages if m["role"] == "system"]
    assert len(systems) == 1 and systems[0]["content"].startswith("你是傲娇猫娘。")


def test_describe_reports_persona_block():
    from app.llm.assembly import describe

    req = _req(config_service=_ConfigService({"p1": PRESET}), config={"persona_preset_id": "p1"})
    rows = describe(req)
    assert rows[0]["block"] == "persona" and rows[0]["role"] == "system"
    assert rows[0]["chars"] == len("你是傲娇猫娘。")


def test_agent_schema_exposes_persona_as_resource_field():
    """配置键与它在前端的可选项必须成对出现（否则用户没法选，只能手抄 ID）。"""
    from app.llm.config import DEFAULT_LLM_CONFIG
    from app.llm.config_schema import SCHEMA

    assert DEFAULT_LLM_CONFIG["persona_preset_id"] == ""
    field = SCHEMA["persona_preset_id"]
    assert field["type"] == "resource" and field["resource"] == "role_preset"
    assert field["default"] == DEFAULT_LLM_CONFIG["persona_preset_id"]
