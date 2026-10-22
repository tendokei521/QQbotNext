"""LLM 组装策略模块：接管 `assembly` 槽（原 agent「行为规则 / 组装档位」那组键）。

## 它接管什么

框架的装配槽（`runtime.assembler`）。装配器本身（12 个命名块、顺序、清洗）
留在框架——那是机制；**"用哪个档位、要不要注入行为规则段、消息怎么格式化、
要不要把每轮请求落盘对账"这些策略**搬到本模块。

## 配置归属

`config_partition.MODULE_KEYS["llm_assembly"]` 的 10 个键由本模块持有。

> 迁移期兼容：模块配置里没有的键回退到 agent 配置（`ConfigView`），
> 所以老用户升级后组装结果不变。
"""

from __future__ import annotations

from typing import Any

from app.plugin import BaseModule

from .config_schema import SCHEMA
from .service import AssemblyAdapter

#: 本模块持有的键（与 `config_partition.MODULE_KEYS["llm_assembly"]` 一致）
OWNED_KEYS: tuple[str, ...] = (
    "assembly_profile",
    "behavior_rules_enable",
    "behavior_rules_override",
    "participation",
    "emoji_react_level",
    "user_message_format",
    "prompt_time_position",
    "system_prompt_style",
    "prompt_dump_enable",
    "prompt_dump_max_entries",
)


class Module(BaseModule):
    name = "组装策略"
    sign = "LlmAssembly"
    description = "组装档位、行为规则段、消息格式与请求落盘对账"
    permission = "everyone"
    subscribe = ()
    category = "LLM"
    order = 20
    provides = ("assembly",)
    supersedes = ("assembly",)
    default_config = {
        "assembly_profile": "blocks",
        "behavior_rules_enable": False,
        "behavior_rules_override": "",
        "participation": "normal",
        "emoji_react_level": 0,
        "user_message_format": "default",
        "prompt_time_position": "user_head",
        "system_prompt_style": "blocks",
        "prompt_dump_enable": False,
        "prompt_dump_max_entries": 20,
    }
    config_schema = SCHEMA

    async def on_load(self) -> None:
        runtime = self._runtime()
        if runtime is None:
            return
        adapter = AssemblyAdapter(self.config, OWNED_KEYS)
        runtime.assembler = adapter
        # `apply_post_assemble` 读它来决定是否落盘请求（prompt_dump_* 归本模块）
        runtime.assembly_options = adapter.view_for(getattr(runtime, "config", None))

    async def on_unload(self) -> None:
        """还原框架默认装配器（租约路径也会做，这里兜底极端情况）。"""
        runtime = self._runtime()
        if runtime is None:
            return
        if isinstance(getattr(runtime, "assembler", None), AssemblyAdapter):
            from app.agent.assembly.assembler import PromptAssembler

            runtime.assembler = PromptAssembler()
        runtime.assembly_options = None

    def _runtime(self) -> Any:
        services = getattr(self, "ctx", None)
        services = getattr(services, "services", None)
        getter = getattr(services, "runtime", None)
        return getter(self.bot_id) if callable(getter) else None

    @property
    def profile(self) -> str:
        return str(self.config.get("assembly_profile", "blocks") or "blocks")


def owned_keys() -> tuple[str, ...]:
    """本模块持有的配置键（供迁移脚本与契约测试核对）。"""
    return OWNED_KEYS


def adapter_for(runtime: Any, config: Any) -> Any:
    return AssemblyAdapter(config, OWNED_KEYS)
