"""AstrBot 组装对齐模块：在单账号下接管消息组装。

`supersedes = ("assembly",)` 让本模块的 `build(req)` 成为该账号的装配器
（启用时装上、禁用/卸载自动还原，多接管者先到先得 + 交棒）——见
`docs/MODULE_DEV.md` §4.7。组装细节与对齐依据见同目录 `assembler.py` / `README.md`。
"""

from __future__ import annotations

import time

from app.plugin import BaseModule, llm_hook

from .assembler import build as build_messages
from .config_schema import SCHEMA

#: 与 `config_schema.SCHEMA` 的 default 一一对应（有测试卡住）
DEFAULT_CONFIG: dict = {
    "persona_preset_id": "",
    "persona_begin_dialogs": True,
    "llm_safety_mode": True,
    "genui_enable": False,
    "tool_call_prompt_mode": "full",
    "router_system_prompt": "",
    "extra_prompt_text": "",
    "extra_prompt_path": "EXTRA_PROMPT.md",
    "local_mode_prompt": "",
    "live_mode": False,
    "web_search_citation_prompt": "",
    "prompt_prefix": "{{prompt}}",
    "identifier": False,
    "group_name_display": False,
    "datetime_system_prompt": True,
    "include_quoted_message": True,
    "include_group_history": True,
    "group_history_max_messages": 50,
    "media_placeholder": True,
    "history_mode": "raw",
}


class Module(BaseModule):
    """用 AstrBot 的组装口径替换框架默认装配器。"""

    name = "AstrBot 组装对齐"
    sign = "AstrBotAlignment"
    description = "在单账号下接管消息组装：system prompt 段落顺序与用户消息块顺序 1:1 对齐 AstrBot"
    category = "LLM"
    permission = "everyone"
    tags = ["LLM", "迁移"]
    order = 10
    #: 声明式接管消息组装（装配器槽）
    supersedes = ("assembly",)
    default_config = DEFAULT_CONFIG
    config_schema = SCHEMA

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        #: 群名缓存 {group_id: (name, fetched_at)}：组装是同步的，群名得预先取好
        self._group_names: dict[str, tuple[str, float]] = {}

    # ---------- 接管槽：装配器 ----------
    def build(self, req) -> list[dict]:
        """框架在每轮组装时调用（`assembly.build_for` → `runtime.assembler.build`）。"""
        return build_messages(self, req)

    # ---------- 组装前把异步素材备好 ----------
    @llm_hook("pre_request", event_type="*", order=95)
    async def prefetch_group_name(self, ctx) -> None:
        """预取群名：`<system_reminder>` 里的 `Group name:` 需要它。

        放在 `pre_request`（组装之前）是因为 `build()` 是同步函数，不能在组装时发网络请求；
        拉取失败只留痕，不影响本轮。
        """
        if not self.config.get("group_name_display", False):
            return
        group_id = getattr(getattr(ctx.event, "group", None), "group_id", None)
        if not group_id:
            return
        key = str(group_id)
        cached = self._group_names.get(key)
        if cached and (time.time() - cached[1]) < 1800:
            return
        try:
            from app.agent.context.group import fetch_group_name

            name = await fetch_group_name(getattr(ctx.event, "bot", None), group_id)
        except Exception as e:  # noqa: BLE001 —— 群名只是提醒段的一部分，失败不该阻断请求
            from app.core.logger import module_logger

            module_logger.add_info(f"#{self.bot_id}").debug(f"[AstrBot对齐] 群名预取失败（已忽略）: {e}")
            return
        if name:
            self._group_names[key] = (str(name), time.time())
