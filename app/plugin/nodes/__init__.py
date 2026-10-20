"""节点框架 + 内置业务节点。

- `base.py`      `MessageNode` / `MessageContext` / `NodeRunner`（一切皆节点）
- `registry.py`  入站链 / 出站链的注册、替换、查询
- `outbound.py`  出站管道（末端是真正发出去的 `SendNode`）
- `inbound.py`   内置入站节点：Router / Permission / Invoke / Agent

依赖方向：`dispatcher` 用本包把消息派发给插件；插件想插自己的节点就注册到
`NodeRegistry`，不要改这里。

（历史上这里是两个地方：`app/nodes/` 放框架、`app/plugin/nodes.py` 放内置节点。
名字撞车且互相 import，P5 合并成一个包。）
"""

from app.plugin.nodes.base import MessageContext, MessageNode, Next, NodeRunner
from app.plugin.nodes.inbound import (
    AgentNode,
    ModuleInvokeNode,
    ModulePermissionNode,
    ModuleRouterNode,
)
from app.plugin.nodes.outbound import OutboundPipeline, SendNode
from app.plugin.nodes.registry import NodeRegistry

__all__ = [
    "MessageContext",
    "MessageNode",
    "Next",
    "NodeRunner",
    "NodeRegistry",
    "OutboundPipeline",
    "SendNode",
    "AgentNode",
    "ModuleInvokeNode",
    "ModulePermissionNode",
    "ModuleRouterNode",
]
