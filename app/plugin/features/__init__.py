"""能力注册表：哪些**机制**能被接管、此刻被谁接管。

v3 起只登记"高级接口"（更换机制 / 接管行为）：
`assembly` / `provider_invoker` / `output` / `agent`。

业务能力（记忆/知识库/主动消息/定时任务/OneBot/MCP/元信息/指代）不在此列——
它们由模块用基础接口实现（`context_providers` / `tool_sources` / 钩子 / `llm_turn` 等）。

对外仍然只暴露下面这些名字，`from app.plugin.features import FeatureRegistry` 不变。
"""

from app.plugin.features.base import FeatureController, FeatureLease
from app.plugin.features.controllers import (
    AgentFeatureController,
    AssemblerFeatureController,
    ConfigToggleFeature,
    OutputFeatureController,
    ProviderInvokerFeatureController,
    RuntimeSlotFeatureController,
)
from app.plugin.features.registry import FeatureRegistry

__all__ = [
    "FeatureController",
    "FeatureLease",
    "FeatureRegistry",
    "ConfigToggleFeature",
    "AgentFeatureController",
    "RuntimeSlotFeatureController",
    "AssemblerFeatureController",
    "ProviderInvokerFeatureController",
    "OutputFeatureController",
]
