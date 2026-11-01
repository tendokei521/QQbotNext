"""提示词组装模块：把「组装策略」与「消息元信息」合并成一组提示词策略（21 键）。

## 合并来源

| 原模块 | 键数 | 负责 |
|---|---|---|
| `llm_assembly` | 7 | 行为规则 / 参与度 / 表情提示 / 消息格式 / 时间位置 / 请求对账 |
| `llm_message_meta` | 14 | 消息元信息渲染（时间 / 发送者 / 引用 / @）与历史清洗 |

两组合并后**键名与默认值一字未改**，装配/渲染实现原样搬进 `src/`，行为不变。

## 它接管什么

框架的装配槽（`runtime.assembler`）。装配器本身（12 个命名块、顺序、清洗）
留在框架——那是机制；**"用哪个档位、要不要注入行为规则段、消息怎么格式化、
要不要把每轮请求落盘对账"这些策略**归本模块。

元信息那半只影响**本轮的文本渲染**，用现成的 `pre_request` 钩子 + 配置覆盖即可：
模块接管 = 设置 `runtime.meta_options` 这个配置视图，并注册两个渲染钩子。

## 配置归属

原 `config_partition.MODULE_KEYS["llm_assembly"]`（7 键）与
`MODULE_KEYS["llm_message_meta"]`（14 键）由本模块持有。

> 迁移期兼容：模块配置里没有的键回退到 agent 配置（`ConfigView`），
> 所以老用户升级后组装结果不变。
"""

from __future__ import annotations

from typing import Any

from app.agent.delivery.output import ConfigView
from app.plugin import BaseModule

from .config_schema import SCHEMA
from .src.assembly import AssemblyAdapter

#: 组装侧键（原 `llm_assembly`）
ASSEMBLY_KEYS: tuple[str, ...] = (
    "behavior_rules_override",
    "participation",
    "emoji_react_level",
    "user_message_format",
    "prompt_time_position",
    "prompt_dump_enable",
    "prompt_dump_max_entries",
)

#: 元信息侧键（原 `llm_message_meta`）
META_KEYS: tuple[str, ...] = (
    "include_time",
    "include_private_qq",
    "include_sender",
    "include_mentioned",
    "include_quote",
    "include_quote_sender",
    "include_sent",
    "fetch_at_nickname",
    "fetch_quote_content",
    "meta_sender_style",
    "meta_sent_style",
    "meta_instruction_mode",
    "meta_mask_nickname",
    "clean_output_parentheses",
)

#: 本模块持有的键（组装 7 + 元信息 14）
OWNED_KEYS: tuple[str, ...] = ASSEMBLY_KEYS + META_KEYS


class Module(BaseModule):
    name = "提示词组装"
    sign = "LlmPrompt"
    description = "控制每轮回复怎么拼、按什么格式发给模型，并给消息补上时间、发送者、引用和 @ 信息"
    permission = "everyone"
    subscribe = ()
    category = "LLM"
    order = 20
    #: 接管框架的装配槽（禁用/卸载自动还原成默认装配器）
    provides = ("assembly",)
    supersedes = ("assembly",)
    default_config = {
        # 组装（原 `llm_assembly`）
        "behavior_rules_override": "",
        "participation": "normal",
        "emoji_react_level": 0,
        "user_message_format": "default",
        "prompt_time_position": "user_head",
        "prompt_dump_enable": False,
        "prompt_dump_max_entries": 20,
        # 消息元信息（原 `llm_message_meta`）
        "include_time": True,
        "include_private_qq": False,
        "include_sender": True,
        "include_mentioned": True,
        "include_quote": True,
        "include_quote_sender": True,
        "include_sent": True,
        "fetch_at_nickname": True,
        "fetch_quote_content": True,
        "meta_sender_style": "compact",
        "meta_sent_style": "compact",
        "meta_instruction_mode": "auto",
        "meta_mask_nickname": False,
        "clean_output_parentheses": True,
    }
    config_schema = SCHEMA

    async def on_load(self) -> None:
        runtime = self._runtime()
        if runtime is None:
            return
        # 装配侧：owned 子集只有组装 7 键（元信息 14 键不进装配视图，行为与合并前一致）
        adapter = AssemblyAdapter(self.config, ASSEMBLY_KEYS)
        runtime.assembler = adapter
        # `apply_post_assemble` 读它来决定是否落盘请求（prompt_dump_* 归本模块）
        runtime.assembly_options = adapter.view_for(getattr(runtime, "config", None))
        # 元信息侧：渲染钩子读 `runtime.meta_options`
        runtime.meta_options = ConfigView(
            getattr(runtime, "config", None), self.config, META_KEYS
        )
        # 业务钩子由本模块自己注册（框架不认识"消息元信息"这个概念）
        for stage, order, handler in (
            ("pre_request", -100, self._collect_user_context),
            ("pre_request", 20, self._format_user_context),
        ):
            runtime.llm_hooks.register(
                stage=stage, event_type="*", order=order, handler=handler, module=self
            )
        # 消息格式说明段（L1）归本模块：模块禁用/卸载 → 段随之消失，
        # 不会给模型留下一本"解释不存在格式"的手册。
        from app.agent.assembly.layers import LAYER_SYSTEM_PROMPT
        from app.agent.assembly.sections import Section, register_section

        from .src.prompt import message_meta_section

        register_section(
            runtime,
            Section(
                "message_meta",
                60,
                message_meta_section,
                layer=LAYER_SYSTEM_PROMPT,
                source="llm_prompt",
            ),
        )

    async def _collect_user_context(self, ctx) -> None:
        from .src.meta import collect_user_context

        await collect_user_context(ctx)

    async def _format_user_context(self, ctx) -> None:
        from .src.meta import format_user_context

        await format_user_context(ctx)

    async def on_unload(self) -> None:
        """还原框架默认装配器与元信息视图（租约路径也会做，这里兜底极端情况）。"""
        runtime = self._runtime()
        if runtime is None:
            return
        # 钩子在模块卸载时由 loader 统一注销（`llm_hooks.unregister_module(self)`）
        from app.agent.assembly.sections import unregister_section

        unregister_section(runtime, "message_meta")
        if isinstance(getattr(runtime, "assembler", None), AssemblyAdapter):
            from app.agent.assembly.assembler import PromptAssembler

            runtime.assembler = PromptAssembler()
        runtime.assembly_options = None
        runtime.meta_options = None

    def _runtime(self) -> Any:
        services = getattr(self, "ctx", None)
        services = getattr(services, "services", None)
        getter = getattr(services, "runtime", None)
        return getter(self.bot_id) if callable(getter) else None


def owned_keys() -> tuple[str, ...]:
    """本模块持有的配置键（供迁移脚本与契约测试核对）。"""
    return OWNED_KEYS
