"""能力注册表：哪些能力能被接管、此刻被谁接管。

- `base.py`         协议（`FeatureController`）与凭证（`FeatureLease`）
- `controllers.py`  10 个具体接管者
- `registry.py`     `FeatureRegistry`（登记 / 租约 / 状态）

对外仍然只暴露下面这些名字，`from app.plugin.features import FeatureRegistry` 不变。
"""

from app.plugin.features.base import FeatureController, FeatureLease
from app.plugin.features.controllers import (
    AgentFeatureController,
    AssemblerFeatureController,
    ConfigToggleFeature,
    ContextFeatureController,
    KnowledgeFeatureController,
    MCPFeatureController,
    MemoryFeatureController,
    MessageMetaFeatureController,
    OneBotToolsFeatureController,
    OutputFeatureController,
    PassiveFeatureController,
    ProactiveFeatureController,
    ProviderInvokerFeatureController,
    RuntimeSlotFeatureController,
    ScheduleDriverFeatureController,
    ScheduleFeatureController,
)
from app.plugin.features.registry import FeatureRegistry

__all__ = [
    "FeatureController",
    "FeatureLease",
    "FeatureRegistry",
    "ConfigToggleFeature",
    "ProactiveFeatureController",
    "ScheduleFeatureController",
    "MemoryFeatureController",
    "KnowledgeFeatureController",
    "OneBotToolsFeatureController",
    "AgentFeatureController",
    "RuntimeSlotFeatureController",
    "AssemblerFeatureController",
    "ProviderInvokerFeatureController",
    "OutputFeatureController",
    "PassiveFeatureController",
    "MessageMetaFeatureController",
    "ContextFeatureController",
    "MCPFeatureController",
    "ScheduleDriverFeatureController",
]
