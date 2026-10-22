"""上下文增强模块：接管「指代消解 / 按需展开 / 历史查询权限」这组策略（10 键）。

## 它负责什么

- 指代消解（`referent_*` 6 键）：焦点表、确定性预取、歧义策略；
- 按需展开（`context_expand_enable`）：`【未展开:…】` 标记与 `expand_*` 系列工具；
- 历史查询权限（`history_auto_qq_min_local` / `history_cross_query_enable`）：
  `get_chat_history` 的补拉阈值与跨会话查询白名单。

机制（`context/referent.py`、`tools/context_tools.py`、`group_log`）留在框架，
本模块把**配置**接过来：`on_load` 时挂 `runtime.context_options`，框架侧所有
相关读取都走 `policy_config(runtime, "context")`。

## 为什么不需要新槽

这些都是"读配置决定行为"的增强，没有需要被整体替换的对象；
用配置视图接管即可，卸载后框架回退到 agent 配置（升级平滑）。
"""

from __future__ import annotations

from typing import Any

from app.plugin import BaseModule

from .config_schema import SCHEMA

#: 本模块持有的键（与 `config_partition.MODULE_KEYS["llm_context"]` 一致）
OWNED_KEYS: tuple[str, ...] = (
    "context_expand_enable",
    "referent_resolve_enable",
    "referent_prefetch_enable",
    "referent_ambiguous_policy",
    "referent_prefetch_max",
    "referent_focus_ttl",
    "referent_focus_max",
    "referent_prompt_enable",
    "history_auto_qq_min_local",
    "history_cross_query_enable",
)


class Module(BaseModule):
    name = "上下文增强"
    sign = "LlmContext"
    description = "指代消解、上下文按需展开、群聊/跨会话历史查询权限"
    permission = "everyone"
    subscribe = ()
    category = "LLM"
    order = 40
    provides = ("context",)
    supersedes = ("context",)
    default_config = {
        "context_expand_enable": True,
        "referent_resolve_enable": True,
        "referent_prefetch_enable": True,
        "referent_ambiguous_policy": "fetch_all",
        "referent_prefetch_max": 2,
        "referent_focus_ttl": 1800,
        "referent_focus_max": 12,
        "referent_prompt_enable": True,
        "history_auto_qq_min_local": 4,
        "history_cross_query_enable": True,
    }
    config_schema = SCHEMA

    async def on_load(self) -> None:
        runtime = self._runtime()
        if runtime is None:
            return
        from app.agent.delivery.output import ConfigView

        runtime.context_options = ConfigView(
            getattr(runtime, "config", None), self.config, OWNED_KEYS
        )

    async def on_unload(self) -> None:
        runtime = self._runtime()
        if runtime is None:
            return
        runtime.context_options = None

    def _runtime(self) -> Any:
        services = getattr(getattr(self, "ctx", None), "services", None)
        getter = getattr(services, "runtime", None)
        return getter(self.bot_id) if callable(getter) else None

    @property
    def expand_enabled(self) -> bool:
        return bool(self.config.get("context_expand_enable", True))


def owned_keys() -> tuple[str, ...]:
    """本模块持有的配置键（供迁移脚本与契约测试核对）。"""
    return OWNED_KEYS
