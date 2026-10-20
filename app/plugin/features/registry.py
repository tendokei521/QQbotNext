"""`FeatureRegistry`：能力接管的登记处。

记录「哪个能力此刻被谁接管」，并发出 `FeatureLease` 凭证；`status()` 供 WebUI
展示接管情况。
"""

from __future__ import annotations

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
- ``onebot_tools``：OneBot 工具总开关
- ``agent``：框架级 Agent 整体启停
- ``assembly``：消息组装器（插件可整体替换本轮 messages 的组装）
- ``provider_invoker``：provider 调用策略（插件可整体替换调用约定/工具循环位置）
"""


from typing import Any

from app.core.logger import logger
from app.plugin.features.base import FeatureController, FeatureLease


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
            snapshot = controller.suppress_with_owner(bot_id, owner)
        else:
            # 已处于被接管状态：新租约不需要再次 suppress，只需记录占有者；
            # 生效的仍是**第一个**租约持有者（多租约「先到先得」语义）。
            snapshot = leases[0].snapshot
            controller.on_owner_changed(bot_id, leases[0].owner)
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
            # 交棒：生效者换成仍在场的下一个租约持有者（如 assembly 要把装配器指回它）
            controller = self.get(feature_id)
            if controller is not None:
                controller.on_owner_changed(bot_id, remaining[0].owner)
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
                # 交棒：与 release() 同一语义（模块卸载/热重载走这条路径）
                controller = self.get(original[0].feature_id if original else "")
                if controller is not None and original:
                    controller.on_owner_changed(original[0].bot_id, remaining[0].owner)
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
