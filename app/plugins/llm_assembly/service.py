"""组装策略适配器：接管 `assembly` 槽，用**模块自己的配置**装配 messages。

## 做法

框架的 `PromptAssembler` 里所有可配项都走 `PromptRequest.cfg(key)`，
而 `cfg()` 读的是 `req.config`。所以这里把 `req.config` 临时换成
`ConfigView(base=原req.config, module_config)` —— 装配器一行不改，
`assembly_profile` / 行为规则 / 表情提示 / 消息格式 / 时间位置 / 组装对账
这些键就从 agent 配置搬到了本模块。

`prompt_dump_*` 由 `apply_post_assemble` 读取，那条路径认
`runtime.assembly_options`（本模块在 `on_load` 里设置）。
"""

from __future__ import annotations

from typing import Any

from app.agent.assembly.assembler import PromptAssembler
from app.agent.delivery.output import ConfigView


class AssemblyAdapter:
    """实现装配槽契约：``build(req) -> list[dict]``。"""

    def __init__(self, config: Any, owned_keys: Any) -> None:
        self.module_config = config
        self.owned_keys = tuple(owned_keys or ())

    def view_for(self, base: Any) -> ConfigView:
        """把模块配置叠在 base（当轮 `req.config`）之上。"""
        return ConfigView(base, self.module_config, self.owned_keys)

    def build(self, req: Any) -> list[dict]:
        """按模块配置装配本轮 messages。"""
        base = getattr(req, "config", None)
        original = base
        try:
            req.config = self.view_for(base)
            return PromptAssembler().build(req)
        finally:
            # 装配是本轮的临时视图，撤掉以免污染后续 post_assemble / dump 的读取
            req.config = original

    def describe(self) -> dict:
        """当前生效的组装档位（排查用）。"""
        getter = getattr(self.module_config, "get", None)
        get = getter if callable(getter) else (lambda k, d=None: d)
        return {
            "profile": str(get("assembly_profile", "blocks") or "blocks"),
            "behavior_rules": bool(get("behavior_rules_enable", False)),
            "message_format": str(get("user_message_format", "") or ""),
            "owned_keys": len(self.owned_keys),
        }
