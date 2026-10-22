"""节点注册表：入站链 / 出站链的节点注册、替换、查询。

- 入站链：消息进入模块前的处理（权限/限流/…），末端由 ModuleInvokeNode 派发业务模块；
- 出站链：Bot 发送消息时的拦截（过滤/改写/…），末端由 SendNode 实际发送。
- 框架与模块均可注册节点，实现「流程修改插入」。

## 归属（owner）

模块注册节点时传 ``owner=模块实例``；该模块卸载时由
`app/plugin/registry.py` 调 `remove_owner(instance)` 一次性清理。
不传 owner 的节点（框架内置）永不被自动清理 —— 这与 task_manager 的 owner 口径一致。
"""

from __future__ import annotations

from app.core.logger import logger
from app.plugin.nodes.base import MessageNode


class NodeRegistry:
    def __init__(self, log=None) -> None:
        self._inbound: dict[str, MessageNode] = {}
        self._outbound: dict[str, MessageNode] = {}
        #: 节点名 → 归属者（仅记录显式给了 owner 的；内置节点不在表内）
        self._owners: dict[str, object] = {}
        self.log = log or logger

    # ── 注册 ──────────────────────────────────────────────
    def register(self, node: MessageNode, direction: str = "inbound", *,
                 owner: object | None = None) -> MessageNode:
        """登记节点。给了 ``owner`` 才可被 `remove_owner` 按归属清理。"""
        target = self._inbound if direction == "inbound" else self._outbound
        target[node.name] = node
        if owner is not None:
            self._owners[node.name] = owner
        else:
            self._owners.pop(node.name, None)
        return node

    def register_inbound(self, node: MessageNode, *, owner: object | None = None) -> MessageNode:
        return self.register(node, "inbound", owner=owner)

    def register_outbound(self, node: MessageNode, *, owner: object | None = None) -> MessageNode:
        return self.register(node, "outbound", owner=owner)

    # ── 替换 / 移除 ────────────────────────────────────────
    def replace(self, name: str, node: MessageNode, direction: str = "inbound") -> bool:
        """用新节点替换同名节点（实现「替换内置节点」）。"""
        target = self._inbound if direction == "inbound" else self._outbound
        if name not in target:
            return False
        node.name = node.name or name
        target[name] = node
        self.log.info(f"[Node] 替换 {direction} 节点 {name} -> {node.__class__.__name__}")
        return True

    def remove(self, name: str, direction: str = "inbound") -> bool:
        target = self._inbound if direction == "inbound" else self._outbound
        self._owners.pop(name, None)
        return target.pop(name, None) is not None

    def remove_owner(self, owner: object) -> int:
        """按归属者批量移除节点（模块卸载/热重载用）。返回移除数量。"""
        names = [name for name, value in self._owners.items() if value is owner]
        removed = 0
        for name in names:
            for target in (self._inbound, self._outbound):
                if target.pop(name, None) is not None:
                    removed += 1
            self._owners.pop(name, None)
        if removed:
            self.log.info(f"[Node] 已清理 {removed} 个由模块注册的节点")
        return removed

    # ── 查询 ──────────────────────────────────────────────
    def get(self, name: str, direction: str = "inbound") -> MessageNode | None:
        target = self._inbound if direction == "inbound" else self._outbound
        return target.get(name)

    def inbound_nodes(self) -> list[MessageNode]:
        return sorted(self._inbound.values(), key=lambda n: n.order)

    def outbound_nodes(self) -> list[MessageNode]:
        return sorted(self._outbound.values(), key=lambda n: n.order)
