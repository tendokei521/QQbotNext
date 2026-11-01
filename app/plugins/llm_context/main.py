"""上下文增强模块（重写版，2 键）：让模型能寻址上下文、按需取回。

## 设计（抛弃旧设计）

旧实现靠"焦点表 + 预取候选 + 歧义策略"猜模型指什么；新组装格式里每条消息都带
id（`[msg:id]` / `[reply_to:id]` / `[forward_message:id]` / `[image:id]`），
所以改成 **可寻址 + 按需取回**：

- 一个工具 `get_context(target)`（message/recent/forward/user/image）；
- 一段极短「上下文解析」约定（L1，order 59）；
- 唯一预取：本轮引用的正文自动内联（`prefetch_reply`）。

旧文件（focus/referent/context_summary/context_tools/service）已整体删除。
"""

from __future__ import annotations

from typing import Any

from app.plugin import BaseModule

from .config_schema import SCHEMA

#: 本模块持有的键（2 键）
OWNED_KEYS: tuple[str, ...] = tuple(SCHEMA)


class _ContextToolSource:
    """工具源：向框架贡献 `get_context`。"""

    name = "llm_context"
    order = 40

    def __init__(self, module: "Module") -> None:
        self.owner = module
        self.module = module

    async def collect(self, scope: Any):
        config = getattr(scope, "config", None)
        getter = getattr(config, "get", None)
        enabled = True
        if callable(getter):
            try:
                enabled = bool(getter("context_enable", True))
            except Exception:  # noqa: BLE001
                enabled = True
        if not enabled:
            return []
        from .src.tools import build_context_tool

        return [build_context_tool()]


class Module(BaseModule):
    name = "上下文增强"
    sign = "LlmContext"
    description = "让模型能按 id 取回上下文、解析「刚才/那条」指代"
    permission = "everyone"
    subscribe = ()
    category = "LLM"
    order = 40
    default_config = {key: spec.get("default") for key, spec in SCHEMA.items()}
    config_schema = SCHEMA

    async def on_load(self) -> None:
        runtime = self._runtime()
        if runtime is None:
            return
        from app.agent.assembly.layers import LAYER_SYSTEM_PROMPT
        from app.agent.assembly.sections import Section, register_section
        from app.agent.delivery.output import ConfigView
        from app.agent.loop.providers import register_tool_source

        from .src.convention import convention_section

        runtime.context_options = ConfigView(
            getattr(runtime, "config", None), self.config, OWNED_KEYS
        )
        register_tool_source(runtime, _ContextToolSource(self))
        register_section(
            runtime,
            Section(
                "context_convention",
                59,
                convention_section,
                layer=LAYER_SYSTEM_PROMPT,
                source="llm_context",
            ),
        )
        runtime.llm_hooks.register(
            stage="pre_request", event_type="*", order=-30,
            handler=self._prefetch_reply, module=self,
        )

    async def on_unload(self) -> None:
        runtime = self._runtime()
        if runtime is None:
            return
        from app.agent.assembly.sections import unregister_section
        from app.agent.loop.providers import unregister_owner

        unregister_section(runtime, "context_convention")
        unregister_owner(runtime, self)
        runtime.context_options = None

    async def _prefetch_reply(self, ctx) -> None:
        """把本轮引用的正文取回，交给组装模块填进 reply_to。"""
        if not bool(self.config.get("context_enable", True)):
            return
        if not bool(self.config.get("prefetch_reply", True)):
            return
        event = getattr(ctx, "event", None)
        if event is None or not getattr(event, "message", None):
            return
        reply_id = ""
        for seg in event.message:
            if getattr(seg, "type", "") == "reply":
                reply_id = str(getattr(seg, "data", {}).get("id", "") or "")
                break
        if not reply_id:
            return
        bot = getattr(event, "bot", None) or getattr(ctx, "bot", None)
        if bot is None:
            return
        try:
            resp = await bot.get_msg(reply_id)
        except Exception as e:  # noqa: BLE001 —— 预取失败不阻断本轮
            from app.agent import logger

            logger.debug(f"[上下文] 预取引用失败（reply_id={reply_id}）: {e}")
            return
        data = (resp or {}).get("data", {}) or {}
        sender = data.get("sender", {}) or {}
        message = data.get("message") or []
        renderer = getattr(getattr(ctx, "runtime", None), "message_assembly", None)
        text = ""
        if renderer is not None:
            body = getattr(renderer, "render_segments", None)
            if callable(body):
                try:
                    text = body(message)
                except Exception:  # noqa: BLE001
                    text = ""
        if not text:
            chunks: list[str] = []
            for seg in message:
                stype = seg.get("type") if isinstance(seg, dict) else getattr(seg, "type", "")
                sdata = (seg.get("data") if isinstance(seg, dict) else getattr(seg, "data", {})) or {}
                if stype == "text":
                    chunks.append(str(sdata.get("text", "")))
                elif stype:
                    chunks.append(f"[{stype}]")
            text = "".join(chunks)
        ctx.state["reply_prefetched"] = {
            "id": reply_id,
            "message_id": reply_id,
            "name": sender.get("card") or sender.get("nickname") or "",
            "text": text,
        }

    def _runtime(self) -> Any:
        services = getattr(self, "ctx", None)
        services = getattr(services, "services", None)
        getter = getattr(services, "runtime", None)
        return getter(self.bot_id) if callable(getter) else None


def owned_keys() -> tuple[str, ...]:
    return OWNED_KEYS
