"""上下文增强模块：接管「指代消解 / 按需展开 / 历史查询权限」这组业务（10 键）。

## v3 重写：这是**业务**，不是接管

之前它声明 `supersedes=("context",)`，让 UI 显示"本模块接管了框架的上下文增强"。
但框架里根本不该有"上下文增强机制"——那本来就是业务。重写后：

- 实现（`referent.py` / `focus.py` / `context_tools.py` / `context_summary.py`）
  **整体搬进本模块**；
- 模块只用三个**基础接口**工作：
  - `runtime.context_providers`：每轮贡献「指代块」
  - `runtime.tool_sources`：注册 `expand_*` 展开工具
  - `@llm_hook("pre_request")`：每轮让焦点表衰减
- **不再声明 `provides` / `supersedes`**：框架不认识"上下文增强"这个概念。

## 配置归属

`config_partition.MODULE_KEYS["llm_context"]` 的 10 个键由本模块持有。
"""

from __future__ import annotations

from typing import Any

from app.plugin import BaseModule

from .config_schema import SCHEMA
from .service import ContextToolSource, ReferentProvider, begin_turn

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
    description = "让模型知道「刚才」「那条」指什么，需要时再展开上下文"
    permission = "everyone"
    subscribe = ()
    category = "LLM"
    order = 40
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

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self._providers: list = []

    async def on_load(self) -> None:
        runtime = self._runtime()
        if runtime is None:
            return
        from app.agent.delivery.output import ConfigView
        from app.agent.loop.providers import register_context_provider, register_tool_source

        runtime.context_options = ConfigView(
            getattr(runtime, "config", None), self.config, OWNED_KEYS
        )
        # P6 能力声明：本模块**提供**"上下文按需展开"这项能力。框架此后只问
        # "有没有人提供"，不再靠读 `context_expand_enable` 猜（配置仍表达偏好）。
        from app.agent.capabilities import CAP_CONTEXT_EXPAND, registry_of

        registry_of(runtime).declare_tool(CAP_CONTEXT_EXPAND, source="llm_context")
        provider = ReferentProvider(self)
        source = ContextToolSource(self)
        register_context_provider(runtime, provider)
        register_tool_source(runtime, source)
        self._providers = [provider, source]
        # 每轮让焦点表衰减（原来在框架 tool_source 里硬调）
        runtime.llm_hooks.register(
            stage="pre_request", event_type="*", order=-50,
            handler=self._begin_turn, module=self,
        )

    async def on_unload(self) -> None:
        runtime = self._runtime()
        if runtime is None:
            return
        from app.agent.loop.providers import unregister_owner

        # 取消能力声明：框架立刻回到"没人提供"（配置兜底仍在，行为平滑）
        from app.agent.capabilities import CAP_CONTEXT_EXPAND, registry_of

        registry_of(runtime).revoke_tool(CAP_CONTEXT_EXPAND, source="llm_context")
        unregister_owner(runtime, self)
        self._providers = []
        runtime.context_options = None

    async def _begin_turn(self, ctx) -> None:
        from app.agent.loop.providers import TurnScope

        begin_turn(
            TurnScope(
                runtime=getattr(ctx, "runtime", None),
                config=getattr(getattr(ctx, "runtime", None), "config", None),
                session_id=getattr(ctx, "session_id", ""),
                user_text=getattr(ctx, "user_text", ""),
            )
        )

    def tool_context_for(self, scope: Any) -> Any:
        """给 `context_tools` 用的 ``ToolContext``（无事件路径也要能用）。"""
        from app.agent.tools.spec import ToolContext

        runtime = getattr(scope, "runtime", None)
        return ToolContext(
            module=self,
            bot=self._bot(),
            session_id=getattr(scope, "session_id", ""),
            event=getattr(scope, "event", None),
            runtime=runtime,
            user_id=getattr(scope, "user_id", None),
            group_id=getattr(scope, "group_id", None),
        )

    def _runtime(self) -> Any:
        services = getattr(getattr(self, "ctx", None), "services", None)
        getter = getattr(services, "runtime", None)
        return getter(self.bot_id) if callable(getter) else None

    def _bot(self) -> Any:
        runtime = self._runtime()
        live = getattr(runtime, "bot", None) if runtime is not None else None
        return live or getattr(getattr(self, "ctx", None), "bot", None)

    @property
    def expand_enabled(self) -> bool:
        return bool(self.config.get("context_expand_enable", True))


def owned_keys() -> tuple[str, ...]:
    """本模块持有的配置键（供迁移脚本与契约测试核对）。"""
    return OWNED_KEYS
