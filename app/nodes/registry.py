"""节点注册表：入站链 / 出站链的节点注册、替换、查询。

- 入站链：消息进入模块前的处理（权限/限流/…），末端由 ModuleInvokeNode 派发业务模块；
- 出站链：Bot 发送消息时的拦截（过滤/改写/…），末端由 SendNode 实际发送。
框架与模块均可注册节点，实现「流程修改插入」。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from app.core.logger import logger
from app.nodes.base import MessageNode


class NodeRegistry:
    def __init__(self, log=None) -> None:
        self._inbound: Dict[str, MessageNode] = {}
        self._outbound: Dict[str, MessageNode] = {}
        self.log = log or logger

    # ── 注册 ──────────────────────────────────────────────
    def register(self, node: MessageNode, direction: str = "inbound") -> MessageNode:
        target = self._inbound if direction == "inbound" else self._outbound
        target[node.name] = node
        return node

    def register_inbound(self, node: MessageNode) -> MessageNode:
        return self.register(node, "inbound")

    def register_outbound(self, node: MessageNode) -> MessageNode:
        return self.register(node, "outbound")

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
        return target.pop(name, None) is not None

    # ── 查询 ──────────────────────────────────────────────
    def get(self, name: str, direction: str = "inbound") -> Optional[MessageNode]:
        target = self._inbound if direction == "inbound" else self._outbound
        return target.get(name)

    def inbound_nodes(self) -> List[MessageNode]:
        return sorted(self._inbound.values(), key=lambda n: n.order)

    def outbound_nodes(self) -> List[MessageNode]:
        return sorted(self._outbound.values(), key=lambda n: n.order)
