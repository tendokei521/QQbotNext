"""提示词组装模块（精简版，8 键）。

## 只做两件事

1. **提示词注入**：`behavior_rules_override`（整段替换行为规则）、`participation`（参与度）；
2. **消息组装方式**：本轮消息格式（单行/多行）、时间位置、发送者写法、引用与 @ 的处理、
   正文标注、消歧说明。

原来的 21 键是「组装 7 + 消息元信息 14」两组合并而来，其中 12 个开关属于
"同一件事的不同旋钮"（要不要时间/时间放哪、要不要昵称/昵称怎么写/要不要脱敏、
要不要引用/要不要被引用正文/要不要 @ 昵称……），现在分别收敛成
`prompt_time_position` / `sender_label` / `quote_and_at` 三个档位选择。

表情活跃度、括号清洗、请求落盘已从本模块移出并删除（2026-11-01 精简）。
"""

from __future__ import annotations

from typing import Any

from app.agent.delivery.output import ConfigView
from app.plugin import BaseModule

from .config_schema import SCHEMA

#: 本模块持有的配置键（8 键，见 config_schema）
OWNED_KEYS: tuple[str, ...] = tuple(SCHEMA)


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
    default_config = {key: spec.get("default") for key, spec in SCHEMA.items()}
    config_schema = SCHEMA

    async def on_load(self) -> None:
        runtime = self._runtime()
        if runtime is None:
            return
        from app.agent.assembly.layers import LAYER_SYSTEM_PROMPT
        from app.agent.assembly.sections import Section, register_section

        from .src.assembly import AssemblyAdapter
        from .src.prompt import message_meta_section

        adapter = AssemblyAdapter(self.config, OWNED_KEYS)
        runtime.assembler = adapter
        runtime.assembly_options = adapter.view_for(getattr(runtime, "config", None))
        # 元信息渲染与装配都读这 8 个键：一个视图即可，不再拆两套 owned 子集
        runtime.meta_options = ConfigView(
            getattr(runtime, "config", None), self.config, OWNED_KEYS
        )
        for stage, order, handler in (
            ("pre_request", -100, self._collect_user_context),
            ("pre_request", 20, self._format_user_context),
        ):
            runtime.llm_hooks.register(
                stage=stage, event_type="*", order=order, handler=handler, module=self
            )
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
        from app.agent.assembly.sections import unregister_section

        from .src.assembly import AssemblyAdapter

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
