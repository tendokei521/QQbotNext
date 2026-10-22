"""消息元信息模块：接管「消息元信息 / 历史清洗」这组策略（14 键）。

## 它负责什么

模型看到的"这条消息是谁、什么时候、回了谁、@了谁"——全由本模块的配置决定。
框架侧的机制（`assembly/enhance.py` 的取值与格式化、`assistant_output` 的历史清洗）
一行没改，只是把读取口径换成了 `policy_config(runtime, "meta")`。

## 为什么不需要新槽

这组键只影响**本轮的文本渲染**，用现成的 `pre_request` 钩子 + 配置覆盖即可，
不必造"元信息槽"。模块接管 = 设置 `runtime.meta_options` 这个配置视图。

## 配置归属

`config_partition.MODULE_KEYS["llm_message_meta"]` 的 14 个键。
"""

from __future__ import annotations

from typing import Any

from app.plugin import BaseModule

from .config_schema import SCHEMA

#: 本模块持有的键（与 `config_partition.MODULE_KEYS["llm_message_meta"]` 一致）
OWNED_KEYS: tuple[str, ...] = (
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


class Module(BaseModule):
    name = "消息元信息"
    sign = "LlmMessageMeta"
    description = "用户消息里的时间/发送者/引用/@ 元信息渲染，以及写入历史时的括号清洗"
    permission = "everyone"
    subscribe = ()
    category = "LLM"
    order = 30
    default_config = {
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
        from app.agent.delivery.output import ConfigView

        runtime.meta_options = ConfigView(getattr(runtime, "config", None), self.config, OWNED_KEYS)
        # 业务钩子由本模块自己注册（框架不认识"消息元信息"这个概念）
        for stage, order, handler in (
            ("pre_request", -100, self._collect_user_context),
            ("pre_request", 20, self._format_user_context),
        ):
            runtime.llm_hooks.register(
                stage=stage, event_type="*", order=order, handler=handler, module=self
            )

    async def _collect_user_context(self, ctx) -> None:
        from .service import collect_user_context

        await collect_user_context(ctx)

    async def _format_user_context(self, ctx) -> None:
        from .service import format_user_context

        await format_user_context(ctx)

    async def on_unload(self) -> None:
        runtime = self._runtime()
        if runtime is None:
            return
        # 钩子在模块卸载时由 loader 统一注销（`llm_hooks.unregister_module(self)`）
        runtime.meta_options = None

    def _runtime(self) -> Any:
        services = getattr(getattr(self, "ctx", None), "services", None)
        getter = getattr(services, "runtime", None)
        return getter(self.bot_id) if callable(getter) else None


def owned_keys() -> tuple[str, ...]:
    """本模块持有的配置键（供迁移脚本与契约测试核对）。"""
    return OWNED_KEYS
