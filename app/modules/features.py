"""全局功能/能力注册表（FeatureRegistry）。

插件扩展性核心：
- 框架内置能力通过 FeatureController 注册为 feature_id；
- 插件声明 ``provides`` / ``supersedes`` 后，在加载/启用/卸载/禁用时自动接管或释放；
- 接管是「租约」：多个插件同时接管同一能力时，只要还有租约持有者，能力就保持禁用；
  最后一个租约释放时自动恢复被接管前的状态。

当前注册的框架能力：
- ``proactive``：框架主动消息
- ``schedule``：框架定时任务
- ``memory``：长期记忆总开关
- ``knowledge``：知识库总开关
- ``napcat_tools``：NapCat 工具总开关
- ``agent``：框架级 Agent 整体启停
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.logger import logger


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

    def restore(self, bot_id: Any, snapshot: dict) -> None:
        """按快照恢复该能力。"""
        raise NotImplementedError


class _RuntimeAccess:
    """按 bot_id 获取 AgentRuntime 的小工具。"""

    def __init__(self, agent_manager: Any) -> None:
        self.agent_manager = agent_manager

    def runtime(self, bot_id: Any):
        if self.agent_manager is None or bot_id is None:
            return None
        return self.agent_manager.get_runtime(bot_id)


class ConfigToggleFeature(_RuntimeAccess, FeatureController):
    """通用配置开关能力：接管时把一组布尔配置置 False，恢复时还原。"""

    keys: tuple[str, ...] = ()

    def __init__(self, agent_manager: Any, *, feature_id: str, label: str, keys: tuple[str, ...]) -> None:
        _RuntimeAccess.__init__(self, agent_manager)
        self.feature_id = feature_id
        self.label = label
        self.keys = tuple(keys)

    def _enabled(self, runtime: Any) -> bool:
        if runtime is None:
            return False
        values = [bool(runtime.config.get(k, False)) for k in self.keys]
        return any(values)

    def status(self, bot_id: Any) -> dict:
        runtime = self.runtime(bot_id)
        return {
            "feature_id": self.feature_id,
            "label": self.label,
            "enabled": self._enabled(runtime) if runtime is not None else False,
            "keys": list(self.keys),
        }

    def suppress(self, bot_id: Any) -> dict:
        runtime = self.runtime(bot_id)
        if runtime is None:
            return {}
        snapshot = {k: runtime.config.get(k) for k in self.keys}
        for key in self.keys:
            runtime.config.set(key, False)
        logger.info(f"[Feature] {self.label} 已由插件接管并暂停 ({bot_id})")
        return snapshot

    def restore(self, bot_id: Any, snapshot: dict) -> None:
        runtime = self.runtime(bot_id)
        if runtime is None or not snapshot:
            return
        for key, value in snapshot.items():
            runtime.config.set(key, value)
        logger.info(f"[Feature] {self.label} 已恢复 ({bot_id})")


class ProactiveFeatureController(ConfigToggleFeature):
    """框架主动消息：同时暂停运行中的计时器，恢复时重新武装。"""

    def __init__(self, agent_manager: Any) -> None:
        super().__init__(
            agent_manager,
            feature_id="proactive",
            label="主动消息",
            keys=("proactive_friend_enable", "proactive_group_enable"),
        )

    def suppress(self, bot_id: Any) -> dict:
        runtime = self.runtime(bot_id)
        if runtime is None:
            return {}
        snapshot = super().suppress(bot_id)
        try:
            runtime.proactive.stop()
        except Exception:
            pass
        return snapshot

    def restore(self, bot_id: Any, snapshot: dict) -> None:
        runtime = self.runtime(bot_id)
        if runtime is None or not snapshot:
            return
        super().restore(bot_id, snapshot)
        try:
            runtime.proactive.resume()
        except Exception:
            pass


class ScheduleFeatureController(ConfigToggleFeature):
    """框架定时任务：暂停运行中的定时器，恢复时重新武装。"""

    def __init__(self, agent_manager: Any) -> None:
        super().__init__(
            agent_manager,
            feature_id="schedule",
            label="定时任务",
            keys=("schedule_enable",),
        )

    def suppress(self, bot_id: Any) -> dict:
        runtime = self.runtime(bot_id)
        if runtime is None:
            return {}
        snapshot = super().suppress(bot_id)
        try:
            runtime.scheduler.stop()
        except Exception:
            pass
        return snapshot

    def restore(self, bot_id: Any, snapshot: dict) -> None:
        runtime = self.runtime(bot_id)
        if runtime is None or not snapshot:
            return
        super().restore(bot_id, snapshot)
        try:
            runtime.scheduler.resume()
        except Exception:
            pass


class MemoryFeatureController(ConfigToggleFeature):
    """长期记忆总开关。"""

    def __init__(self, agent_manager: Any) -> None:
        super().__init__(
            agent_manager,
            feature_id="memory",
            label="长期记忆",
            keys=("memory_enable",),
        )


class KnowledgeFeatureController(ConfigToggleFeature):
    """知识库总开关。"""

    def __init__(self, agent_manager: Any) -> None:
        super().__init__(
            agent_manager,
            feature_id="knowledge",
            label="知识库",
            keys=("knowledge_enable",),
        )


class NapcatToolsFeatureController(ConfigToggleFeature):
    """NapCat 工具总开关。"""

    def __init__(self, agent_manager: Any) -> None:
        super().__init__(
            agent_manager,
            feature_id="napcat_tools",
            label="NapCat Tools",
            keys=("napcat_tools_enable",),
        )


class AgentFeatureController(_RuntimeAccess, FeatureController):
    """框架 Agent 整体启停（模块 authority 层面的接管）。"""

    feature_id = "agent"
    label = "Agent"

    def status(self, bot_id: Any) -> dict:
        runtime = self.runtime(bot_id)
        return {
            "feature_id": self.feature_id,
            "label": self.label,
            "enabled": bool(runtime.config.enabled) if runtime is not None else False,
        }

    def suppress(self, bot_id: Any) -> dict:
        runtime = self.runtime(bot_id)
        if runtime is None:
            return {}
        snapshot = {"enabled": bool(runtime.config.enabled)}
        runtime.config.set_enabled(False)
        try:
            runtime.proactive.stop()
        except Exception:
            pass
        try:
            runtime.scheduler.stop()
        except Exception:
            pass
        logger.info(f"[Feature] Agent 已由插件接管并暂停 ({bot_id})")
        return snapshot

    def restore(self, bot_id: Any, snapshot: dict) -> None:
        runtime = self.runtime(bot_id)
        if runtime is None or not snapshot:
            return
        runtime.config.set_enabled(bool(snapshot.get("enabled", True)))
        try:
            runtime.proactive.resume()
        except Exception:
            pass
        try:
            runtime.scheduler.resume()
        except Exception:
            pass
        logger.info(f"[Feature] Agent 已恢复 ({bot_id})")


@dataclass
class FeatureLease:
    """一个插件对某个 feature 的接管租约。"""

    feature_id: str
    bot_id: Any
    owner: Any
    snapshot: Any = None
    providers: tuple[str, ...] = field(default_factory=tuple)


class FeatureRegistry:
    """全局能力注册表：注册框架能力，并管理插件接管租约。"""

    def __init__(self) -> None:
        self._controllers: dict[str, FeatureController] = {}
        self._leases: dict[str, list[FeatureLease]] = {}

    # ── 注册 ─────────────────────────────────────────────
    def register(self, controller: FeatureController) -> None:
        if not controller.feature_id or controller.feature_id in self._controllers:
            raise ValueError(f"Feature {controller.feature_id!r} 已注册或缺少 feature_id")
        self._controllers[controller.feature_id] = controller

    def unregister(self, feature_id: str) -> None:
        self._controllers.pop(feature_id, None)

    def get(self, feature_id: str) -> FeatureController | None:
        return self._controllers.get(feature_id)

    def list_features(self) -> list[dict]:
        return [
            {
                "feature_id": c.feature_id,
                "label": c.label,
            }
            for c in self._controllers.values()
        ]

    # ── 租约 ─────────────────────────────────────────────
    @staticmethod
    def _key(feature_id: str, bot_id: Any) -> str:
        return f"{feature_id}\x1f{bot_id}"

    def is_suppressed(self, feature_id: str, bot_id: Any) -> bool:
        return bool(self._leases.get(self._key(feature_id, bot_id)))

    def suppress(self, feature_id: str, owner: Any, bot_id: Any) -> dict:
        controller = self.get(feature_id)
        if controller is None:
            logger.warning(f"[Feature] 尝试接管未知能力 {feature_id!r} (bot {bot_id})")
            return {}
        if bot_id is None:
            logger.warning(f"[Feature] 全局实例不能接管 Bot 级能力 {feature_id} ({bot_id})")
            return {}
        key = self._key(feature_id, bot_id)
        leases = self._leases.setdefault(key, [])
        if any(lease.owner is owner for lease in leases):
            return {}

        snapshot = None
        owners = [getattr(lease.owner, "name", getattr(lease.owner, "module_name", "插件"))
                  for lease in leases]
        if not leases:
            snapshot = controller.suppress(bot_id)
        else:
            # 已处于被接管状态：新租约不需要再次 suppress，只需记录占有者
            snapshot = leases[0].snapshot
        leases.append(FeatureLease(feature_id=feature_id, bot_id=bot_id, owner=owner, snapshot=snapshot))
        owner_label = getattr(owner, "name", getattr(owner, "module_name", str(owner)))
        logger.info(f"[Feature] {controller.label} 被 {owner_label} 接管 (bot {bot_id})，当前租约: {owners + [owner_label]}")
        return {"snapshot": snapshot, "owners": owners + [owner_label]}

    def release(self, feature_id: str, owner: Any, bot_id: Any) -> bool:
        key = self._key(feature_id, bot_id)
        original = self._leases.get(key)
        if not original:
            return False
        before = len(original)
        remaining = [lease for lease in original if lease.owner is not owner]
        removed = before - len(remaining)
        if removed == 0:
            return False
        if remaining:
            self._leases[key] = remaining
            return True
        self._leases.pop(key, None)
        snapshot_value = original[0].snapshot if original else {}
        controller = self.get(feature_id)
        if controller is not None:
            try:
                controller.restore(bot_id, snapshot_value or {})
            except Exception as e:
                logger.warning(f"[Feature] 恢复 {feature_id} 失败: {e}")
        return True

    def release_owner(self, owner: Any) -> int:
        """释放某插件持有的全部租约。"""
        count = 0
        for key in list(self._leases.keys()):
            original = self._leases[key]
            before = len(original)
            remaining = [lease for lease in original if lease.owner is not owner]
            if len(remaining) == before:
                continue
            count += 1
            if remaining:
                self._leases[key] = remaining
                continue
            self._leases.pop(key, None)
            snapshot_value = original[0].snapshot if original else {}
            controller = self.get(original[0].feature_id if original else "")
            if controller is not None:
                try:
                    controller.restore(original[0].bot_id if original else None, snapshot_value or {})
                except Exception as e:
                    logger.warning(f"[Feature] 恢复 {original[0].feature_id} 失败: {e}")
        return count

    def acquire_module(self, module: Any) -> int:
        """模块加载/启用时，按 supersedes 自动接管；已禁用模块不接管。"""
        if getattr(module, "bot_id", None) is None:
            return 0
        if not getattr(getattr(module, "authority", None), "enabled", True):
            return 0
        count = 0
        for feature_id in getattr(module, "supersedes", ()) or ():
            if self.suppress(feature_id, module, module.bot_id):
                count += 1
        return count

    def release_module(self, module: Any) -> int:
        """模块卸载/禁用时，释放该模块持有的全部租约。"""
        return self.release_owner(module)

    # ── 查询 ─────────────────────────────────────────────
    def query(self, feature_id: str, bot_id: Any) -> dict:
        controller = self.get(feature_id)
        if controller is None:
            return {"feature_id": feature_id, "registered": False, "enabled": False, "owners": []}
        key = self._key(feature_id, bot_id)
        leases = self._leases.get(key, [])
        status = controller.status(bot_id) if bot_id is not None else {}
        return {
            **status,
            "registered": True,
            "suppressed": bool(leases),
            "owners": [
                {
                    "module": getattr(lease.owner, "module_name", getattr(lease.owner, "name", "?")),
                    "name": getattr(lease.owner, "name", "?"),
                    "bot_id": lease.bot_id,
                }
                for lease in leases
            ],
        }

    def status(self, bot_id: Any = None) -> list[dict]:
        return [self.query(c.feature_id, bot_id) for c in self._controllers.values()]
