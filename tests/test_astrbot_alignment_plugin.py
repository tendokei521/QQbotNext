"""AstrBot 组装对齐插件：逐字对齐上游的 system prompt 与请求形态。

对齐基准来自 `1/AstrBot-master`（v4.27.2），本文件断言的是**拼接结果的字面量**——
不是"大致包含"，而是逐字符相等（分隔符、空行、`Belows` 这类上游拼写错误都要一致）。
"""

from __future__ import annotations

import re
from types import SimpleNamespace

from app.llm.assembly import PromptRequest
from app.modules.base import ModuleAuthority, ModuleConfig, ModuleContext, ServiceAccess
from app.modules.features import AssemblerFeatureController, FeatureRegistry
from module.modules.astrbot_alignment import assembler
from module.modules.astrbot_alignment.astrbot_prompts import (
    GROUP_HISTORY_DIRECTED_AT_YOU,
    GROUP_HISTORY_FOOTER,
    GROUP_HISTORY_HEADER,
    LLM_SAFETY_MODE_SYSTEM_PROMPT,
    PERSONA_SECTION_TITLE,
    TOOL_CALL_PROMPT,
    TOOL_CALL_PROMPT_SKILLS_LIKE_MODE,
)
from module.modules.astrbot_alignment.config_schema import SCHEMA
from module.modules.astrbot_alignment.module import DEFAULT_CONFIG, Module

# ==================== 夹具 ====================


class _ConfigSvc:
    def __init__(self, data):
        self.data = data

    def get_module_config(self, module, bot_id):
        return self.data

    def set_module_config(self, module, bot_id, data, persist=True):
        self.data = data


class _AuthSvc:
    def get_module_authority(self, module, bot_id):
        return {}

    def set_module_authority(self, module, bot_id, authority):
        return None


class _RolePresets:
    def __init__(self, preset=None):
        self.preset = preset

    def get_preset(self, role_id):
        return self.preset


class _HistoryStore:
    """假的历史存储：history_snapshot 返回原始条目（框架契约的形状）。"""

    def __init__(self, entries=None):
        self.entries = list(entries or [])

    def history_snapshot(self, session_id, **kwargs):
        return [dict(e) for e in self.entries]


def _make(overrides=None, role_presets=None):
    data = {**DEFAULT_CONFIG, **(overrides or {})}
    config = ModuleConfig("astrbot_alignment", "bot_as", dict(DEFAULT_CONFIG), _ConfigSvc(data))
    authority = ModuleAuthority("astrbot_alignment", "bot_as", _AuthSvc())
    services = ServiceAccess(role_presets=role_presets if role_presets is not None else _RolePresets())
    ctx = ModuleContext(
        module_name="astrbot_alignment", bot_id="bot_as", config=config, authority=authority, services=services
    )
    return Module(ctx)


def _event(message=None, **kw):
    defaults = dict(
        event_type="message_group",
        self_id=42,
        user_id=10001,
        message=message if message is not None else [{"type": "text", "data": {"text": "你好"}}],
        user=SimpleNamespace(card="", nickname="甲"),
        group=SimpleNamespace(group_id=778),
        bot=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _req(module, *, event=None, user_context=None, history=None, session_mgr=None, **kw):
    runtime = SimpleNamespace(bot_id=42, session_mgr=session_mgr or _HistoryStore())
    base = dict(
        runtime=runtime,
        event=event if event is not None else _event(),
        ctx=SimpleNamespace(state={"user_context": user_context} if user_context else {}),
        config={"system_prompt": "你是助手"},
        session_id="group_778",
        user_text="你好",
        raw_user_text="你好",
        history=list(history or []),
        available_tools=set(),
        group_id="778",
        user_id="10001",
    )
    base.update(kw)
    return PromptRequest(**base)


def _system(messages):
    return messages[0]["content"]


# ==================== system prompt：段落顺序与分隔符 ====================


def test_system_prompt_verbatim_order():
    """安全段前插在最顶，其后依次是 persona → skills → 工具调用段。"""
    module = _make()
    req = _req(module, skill_blocks=["技能A"], available_tools={"t1"})

    expected = (
        f"{LLM_SAFETY_MODE_SYSTEM_PROMPT}\n\n"
        f"\n{PERSONA_SECTION_TITLE}\n\n你是助手\n"
        "\n技能A\n"
        f"\n{TOOL_CALL_PROMPT}\n"
    )
    assert assembler.build_system_prompt(module, req) == expected


def test_system_prompt_without_safety_and_optional_sections():
    """关掉安全段后不再前插；各可选段按顺序追加。"""
    module = _make({
        "llm_safety_mode": False,
        "router_system_prompt": "路由说明",
        "local_mode_prompt": "本地环境说明",
        "live_mode": True,
        "web_search_citation_prompt": "引用格式",
        "genui_enable": True,
    })
    req = _req(module, available_tools={"t1"})

    text = assembler.build_system_prompt(module, req)
    assert LLM_SAFETY_MODE_SYSTEM_PROMPT not in text
    assert text.startswith("\n\n[ChatUI HTML GenUI]\n")
    # persona → router → local → 工具段 → live → 引用段
    order = [
        f"\n{PERSONA_SECTION_TITLE}\n\n你是助手\n",
        "\n路由说明\n",
        "\n本地环境说明\n",
        f"\n{TOOL_CALL_PROMPT}\n",
    ]
    positions = [text.index(chunk) for chunk in order]
    assert positions == sorted(positions)
    assert text.index("引用格式") > positions[-1]


def test_extra_prompt_section_verbatim_wrapper():
    module = _make({"extra_prompt_text": "额外要求", "extra_prompt_path": "WS/EXTRA_PROMPT.md"})
    text = assembler.build_system_prompt(module, _req(module))
    assert "[Workspace Extra Prompt]" in text
    assert "The following instructions are loaded from the current workspace `EXTRA_PROMPT.md` file." in text
    assert "From `WS/EXTRA_PROMPT.md`:\n额外要求" in text


def test_tool_call_prompt_modes():
    full = _make({"tool_call_prompt_mode": "full"})
    skills_like = _make({"tool_call_prompt_mode": "skills_like"})
    off = _make({"tool_call_prompt_mode": "off"})
    req = _req(full, available_tools={"t1"})

    assert TOOL_CALL_PROMPT in assembler.build_system_prompt(full, req)
    assert TOOL_CALL_PROMPT_SKILLS_LIKE_MODE in assembler.build_system_prompt(
        skills_like, _req(skills_like, available_tools={"t1"})
    )
    assert TOOL_CALL_PROMPT not in assembler.build_system_prompt(off, _req(off, available_tools={"t1"}))

    no_tools = _req(full, available_tools=set())
    assert TOOL_CALL_PROMPT not in assembler.build_system_prompt(full, no_tools), "本轮没挂工具就不该注入"


def test_persona_from_role_preset_and_begin_dialogs_prepended():
    preset = {
        "id": "p1",
        "system_prompt": "你是猫娘",
        "begin_dialogs": [
            {"role": "user", "content": "在吗"},
            {"role": "assistant", "content": "在的"},
        ],
    }
    module = _make({"persona_preset_id": "p1"}, role_presets=_RolePresets(preset))
    messages = module.build(_req(module))

    assert f"\n{PERSONA_SECTION_TITLE}\n\n你是猫娘\n" in _system(messages)
    assert messages[1] == {"role": "user", "content": "在吗"}, "begin_dialogs 前插到历史最前"
    assert messages[2] == {"role": "assistant", "content": "在的"}
    assert messages[-1]["role"] == "user"


def test_begin_dialogs_can_be_disabled():
    preset = {
        "system_prompt": "人设",
        "begin_dialogs": [{"role": "user", "content": "在吗"}],
    }
    module = _make({"persona_preset_id": "p1", "persona_begin_dialogs": False}, role_presets=_RolePresets(preset))
    messages = module.build(_req(module))
    assert [m["role"] for m in messages] == ["system", "user"]


# ==================== 历史 ====================


def test_history_raw_mode_strips_framework_metadata():
    """AstrBot 的 contexts 只有 role/content，框架的 base/nickname/message_id 不发。"""
    sm = _HistoryStore([
        {
            "role": "user",
            "time": 1700000000,
            "content": "上一句",
            "user_id": "1",
            "nickname": "甲",
            "message_id": "99",
            "base": {"sender_name": "甲", "text": "上一句", "segments": [{"type": "text", "data": {"text": "上一句"}}]},
        },
        {"role": "assistant", "content": "上一答"},
    ])
    module = _make()
    messages = module.build(_req(module, session_mgr=sm))

    assert messages[1] == {"role": "user", "content": "上一句"}
    assert messages[2] == {"role": "assistant", "content": "上一答"}
    assert "base" not in messages[1] and "nickname" not in messages[1]
    assert "发送者：" not in _system(messages) and "发送者：" not in str(messages[1])


def test_history_modes_rendered_and_off():
    rendered = _make({"history_mode": "rendered"})
    req = _req(rendered, history=[{"role": "user", "content": "框架渲染过的"}])
    assert rendered.build(req)[1] == {"role": "user", "content": "框架渲染过的"}

    off = _make({"history_mode": "off"})
    sm = _HistoryStore([{"role": "user", "content": "不该出现"}])
    messages = off.build(_req(off, session_mgr=sm, history=[{"role": "user", "content": "也不该出现"}]))
    assert [m["role"] for m in messages] == ["system", "user"]


def test_history_tolerates_store_errors():
    class _Broken:
        def history_snapshot(self, session_id, **kwargs):
            raise RuntimeError("存储炸了")

    module = _make()
    runtime = SimpleNamespace(bot_id=42, session_mgr=_Broken())
    req = _req(module)
    req.runtime = runtime
    assert [m["role"] for m in module.build(req)] == ["system", "user"], "历史取不到不该阻断本轮"


# ==================== 用户消息 ====================


def test_prompt_prefix_placeholder_and_plain_prefix():
    # 关掉默认开启的时间提醒段，才能断言"单文本块 = 纯字符串"这条降级路径
    placeholder = _make({"prompt_prefix": "用户说：{{prompt}}（完）", "datetime_system_prompt": False})
    assert placeholder.build(_req(placeholder))[-1]["content"] == "用户说：你好（完）"

    plain = _make({"prompt_prefix": "前缀>", "datetime_system_prompt": False})
    assert plain.build(_req(plain))[-1]["content"] == "前缀>你好"

    default = _make({"datetime_system_prompt": False})
    assert default.build(_req(default))[-1]["content"] == "你好", "默认 {{prompt}} 即原样"


def test_user_message_degrades_to_plain_string():
    """无额外块、无媒体、只有正文 → 纯字符串（AstrBot 的向后兼容分支）。"""
    module = _make({"datetime_system_prompt": False})
    message = module.build(_req(module))[-1]
    assert message == {"role": "user", "content": "你好"}


def test_datetime_reminder_default_turns_message_into_blocks():
    """AstrBot 的 datetime_system_prompt 默认开启 → 默认一定带 system_reminder 块。"""
    module = _make()
    content = module.build(_req(module))[-1]["content"]
    assert isinstance(content, list)
    assert content[-1]["text"].startswith("<system_reminder>Current datetime: ")


def test_user_message_becomes_blocks_when_extras_present():
    module = _make({"datetime_system_prompt": False})
    req = _req(module, user_context={"quote": "原话", "quote_sender": "乙(2)"})
    message = module.build(req)[-1]
    assert isinstance(message["content"], list)
    assert message["content"][0] == {"type": "text", "text": "你好"}
    assert message["content"][1]["text"] == "<Quoted Message>\n(乙(2)): 原话\n</Quoted Message>"


def test_quote_block_can_be_disabled():
    module = _make({"include_quoted_message": False, "datetime_system_prompt": False})
    req = _req(module, user_context={"quote": "原话"})
    assert module.build(req)[-1] == {"role": "user", "content": "你好"}


def test_system_reminder_verbatim():
    module = _make({"identifier": True, "group_name_display": True, "datetime_system_prompt": True})
    module._group_names["778"] = ("测试群", 2 ** 31)
    req = _req(module)

    blocks = module.build(req)[-1]["content"]
    content = next(b["text"] for b in blocks if b["text"].startswith("<system_reminder>User ID:"))
    assert re.fullmatch(
        r"<system_reminder>User ID: 10001, Nickname: 甲\n"
        r"Group name: 测试群\n"
        r"Current datetime: \d{4}-\d{2}-\d{2} \d{2}:\d{2} \([^)]*\), Weekday: "
        r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)</system_reminder>",
        content,
    ), content


def test_system_reminder_skips_missing_group_name():
    module = _make({"group_name_display": True, "datetime_system_prompt": False})
    req = _req(module)  # 未预取群名 → 缓存为空
    assert module.build(req)[-1] == {"role": "user", "content": "你好"}


def test_media_placeholders_for_image_and_file():
    module = _make()
    event = _event(message=[
        {"type": "image", "data": {"file": "/tmp/a.png"}},
        {"type": "file", "data": {"name": "报告.pdf", "file": "/tmp/b.pdf"}},
        {"type": "text", "data": {"text": "看看"}},
    ])
    req = _req(module, event=event, user_images=[{"kind": "url", "value": "https://a/1.png"}])
    content = module.build(req)[-1]["content"]

    assert content[0] == {"type": "text", "text": "你好"}
    assert "[Image Attachment: path /tmp/a.png]" in content[1]["text"]
    assert "[File Attachment: name 报告.pdf, path /tmp/b.pdf]" in content[2]["text"]
    assert content[-1] == {"type": "image_url", "image_url": {"url": "https://a/1.png"}}, "图片块在最后"


def test_image_only_message_gets_placeholder():
    module = _make()
    event = _event(message=[{"type": "image", "data": {"file": "/tmp/a.png"}}])
    req = _req(module, event=event, raw_user_text="", user_text="", user_images=[{"kind": "url", "value": "u"}])
    content = module.build(req)[-1]["content"]
    assert content[0] == {"type": "text", "text": "[图片]"}


def test_unsupported_modality_replaces_image_with_text():
    module = _make()
    req = _req(module, user_images=[{"kind": "url", "value": "https://a/1.png"}], modalities=["text"])
    content = module.build(req)[-1]["content"]
    assert all(block["type"] == "text" for block in content)
    assert any(block["text"] == "[Image]" for block in content)


# ==================== 群聊上下文块（原文头尾） ====================


def test_group_history_block_verbatim_header_and_lines():
    sm = _HistoryStore([
        {
            "role": "user",
            "content": "早上好",
            "base": {
                "sender_name": "乙",
                "time": 1700000000,
                "segments": [
                    {"type": "text", "data": {"text": "早上好"}},
                    {"type": "at", "data": {"qq": "42", "name": "机器人"}},
                    {"type": "image", "data": {"file": "x"}},
                ],
            },
        },
        {"role": "assistant", "content": "被过滤：只取 user 条目"},
    ])
    module = _make()
    block = assembler._group_history_block(module, _req(module, session_mgr=sm))

    assert block.startswith(GROUP_HISTORY_HEADER), "头部逐字（含 Belows / BEGIN CONTEXT---）"
    assert block.endswith(GROUP_HISTORY_FOOTER)
    line = block[len(GROUP_HISTORY_HEADER):-len(GROUP_HISTORY_FOOTER)]
    assert line.startswith("[乙/")
    assert GROUP_HISTORY_DIRECTED_AT_YOU in line, "@ 到自己要带标记"
    assert " [At: 机器人]" in line and " [Image]" in line
    assert "被过滤" not in block


def test_group_history_block_absent_for_private_or_disabled():
    module = _make()
    private = _req(module, group_id=None, session_mgr=_HistoryStore([{"role": "user", "content": "x"}]))
    assert assembler._group_history_block(module, private) == ""

    disabled = _make({"include_group_history": False})
    group = _req(disabled, session_mgr=_HistoryStore([{"role": "user", "content": "x"}]))
    assert assembler._group_history_block(disabled, group) == ""


# ==================== 框架特有块不该出现 ====================


def test_framework_only_blocks_are_absent():
    """框架默认装配器会加的定时协议 / 消息格式说明 / 主动性块，这里一个都不该有。"""
    from app.llm.prompt import LEGACY_MESSAGE_META_INSTRUCTION, SCHEDULE_INSTRUCTION

    module = _make()
    system = _system(module.build(_req(module, available_tools={"get_chat_history", "expand_recent"})))
    assert SCHEDULE_INSTRUCTION not in system
    assert LEGACY_MESSAGE_META_INSTRUCTION not in system
    assert "主动性" not in system


# ==================== 与框架接管机制打通 ====================


def test_module_declares_assembly_takeover():
    assert Module.supersedes == ("assembly",)
    assert callable(getattr(Module, "build", None))


def test_registry_takeover_installs_and_restores_module_assembler():
    module = _make()
    runtime = SimpleNamespace(bot_id="bot_as", assembler="框架默认装配器")

    class _Manager:
        def get_runtime(self, bot_id):
            return runtime

    registry = FeatureRegistry()
    registry.register(AssemblerFeatureController(_Manager()))

    assert registry.acquire_module(module) == 1
    assert runtime.assembler is module, "启用后该账号的装配器应指向本模块"
    assert registry.release_module(module) == 1
    assert runtime.assembler == "框架默认装配器"


def test_config_defaults_match_schema():
    """代码默认与 WebUI schema 默认必须一致，否则"界面显示开、实际关"。"""
    for key, default in DEFAULT_CONFIG.items():
        assert key in SCHEMA, f"{key} 缺少 WebUI schema"
        assert SCHEMA[key]["default"] == default, f"{key} 默认值不一致：schema={SCHEMA[key]['default']} code={default}"


# ==================== 端到端：真容器里加载即接管 ====================


async def test_plugin_takes_over_in_real_registry(container):
    """走真实链路：把注册表指向仓库真实模块目录 → 加载 → 接管 → 组装 → 卸载还原。"""
    from pathlib import Path

    from app.infrastructure.config.config_service import ConfigService
    from app.llm.assembly import build_for
    from app.llm.manager import AgentManager
    from app.modules.base import ServiceAccess
    from app.modules.registry import ModuleRegistry
    from app.services.module_install_service import ModuleInstallService

    repo_module_dir = Path(__file__).resolve().parent.parent / "module"
    registry = ModuleRegistry(
        modules_dir=repo_module_dir / "modules",
        plugins_dir=repo_module_dir / "plugins",
        config_service=container.get(ConfigService),
        services=container.get(ServiceAccess),
        install_service=container.get(ModuleInstallService),
    )
    bot_id = 20250929
    runtime = container.get(AgentManager).ensure_runtime(bot_id=bot_id)
    assert runtime is not None

    assert await registry.load_single("astrbot_alignment", bot_id) is True, "插件应能从仓库真实目录加载"
    module = registry.get("astrbot_alignment", bot_id)
    # 注册表会先清模块缓存再重新导入（热重载语义），所以这里不能用 isinstance 比对类对象
    assert module is not None and module.__class__.__name__ == "Module"
    assert module.module_name == "astrbot_alignment" and callable(getattr(module, "build", None))
    assert runtime.assembler is module, "启用后该账号的装配器应指向插件"

    messages = build_for(runtime, _req(module))
    assert len(messages) == 2 and messages[0]["role"] == "system"
    assert f"\n{PERSONA_SECTION_TITLE}\n" in messages[0]["content"], "走的是 AstrBot 口径的 system prompt"

    await registry.unload_single("astrbot_alignment", bot_id)
    assert runtime.assembler is not module, "禁用/卸载后应还原框架默认装配器"
