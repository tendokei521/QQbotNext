"""能力接管的协议与凭证。

- `FeatureController`：一个「能力接管者」的接口（谁接管了「LLM 回复」这类能力）；
- `_RuntimeAccess`：统一从 services 里取 runtime 的小工具；
- `FeatureLease`：登记凭证（dataclass），`release()` 归还能力。

注册的框架能力见 `app/plugin/features/builtin.py`（v3 起只有 4 个：`agent` /
`assembly` / `provider_invoker` / `output`；主动消息、定时任务、记忆、知识库、
OneBot 工具等是**业务**，由各自模块用基础接口实现，不是 feature）。
"""


from dataclasses import dataclass, field
from typing import Any


class FeatureController:
    """框架能力控制器基类。"""

    feature_id: str = ""
    label: str = ""

    def status(self, bot_id: Any) -> dict:
        """返回当前能力状态，供 WebUI/API 展示。"""
        return {"feature_id": self.feature_id, "label": self.label, "enabled": True}

    def suppress(self, bot_id: Any) -> dict:
        """暂停该能力，返回用于恢复的快照。"""
        raise NotImplementedError

    def suppress_with_owner(self, bot_id: Any, owner: Any) -> dict:
        """带租约占有者的接管入口。

        需要知道「谁在接管」的能力（例如 ``assembly``：要把 ``runtime.assembler``
        换成插件自己的装配器）覆盖此方法；默认忽略 owner，与 ``suppress`` 等价，
        因此既有的与第三方控制器无需改动。
        """
        return self.suppress(bot_id)

    def restore(self, bot_id: Any, snapshot: dict) -> None:
        """按快照恢复该能力。"""
        raise NotImplementedError

    def on_owner_changed(self, bot_id: Any, owner: Any) -> None:
        """「交棒」钩子：租约集合变化后，把当前**生效**的占有者切给 ``owner``。

        只对接管是「装上某个对象」的能力有意义（例如 ``assembly`` 要把
        ``runtime.assembler`` 指回仍然在场的租约持有者）；默认不用处理。
        """
        return None


class _RuntimeAccess:
    """按 bot_id 获取 AgentRuntime 的小工具。"""

    def __init__(self, agent_manager: Any) -> None:
        self.agent_manager = agent_manager

    def runtime(self, bot_id: Any):
        if self.agent_manager is None or bot_id is None:
            return None
        return self.agent_manager.get_runtime(bot_id)


@dataclass
class FeatureLease:
    """一个插件对某个 feature 的接管租约。"""

    feature_id: str
    bot_id: Any
    owner: Any
    snapshot: Any = None
    providers: tuple[str, ...] = field(default_factory=tuple)
