"""AstrBot 组装对齐插件的配置表单（WebUI）。

默认值必须与 `module.py` 的 `DEFAULT_CONFIG` 一致（有测试卡这条）。
"""

from __future__ import annotations

SCHEMA: dict = {
    # ── 人设 ──────────────────────────────────────────────
    "persona_preset_id": {
        "type": "resource", "resource": "role_preset",
        "label": "角色预设（可选）",
        "description": "下拉直接选系统里的角色预设；留空则用 Agent 配置里的系统提示词。改预设名/提示词即时生效，不用重新“应用”",
        "default": "",
    },
    "persona_begin_dialogs": {
        "type": "boolean", "label": "把预设对话前插到历史最前",
        "description": "对齐 AstrBot persona._begin_dialogs_processed（astr_main_agent.py:536）",
        "default": True,
    },
    # ── system prompt 各段（顺序即 AstrBot 的拼接顺序） ─────
    "llm_safety_mode": {
        "type": "boolean", "label": "安全模式段",
        "description": "在最顶部前插 AstrBot 原文的 Safe Mode 段（LLM_SAFETY_MODE_SYSTEM_PROMPT）",
        "default": True,
    },
    "genui_enable": {
        "type": "boolean", "label": "ChatUI GenUI 段",
        "description": "AstrBot 里 WebChat 默认开启；本框架默认关闭（无 ChatUI）",
        "default": False,
    },
    "tool_call_prompt_mode": {
        "type": "select", "label": "工具调用段",
        "description": "仅本轮挂了工具时注入；full/skills_like 分别是 AstrBot 的两份原文常量",
        "default": "full",
        "options": {"full": "完整版（full）", "skills_like": "skills-like 版", "off": "不注入"},
    },
    "router_system_prompt": {
        "type": "string", "label": "Subagent router 段（可选）",
        "description": "对齐 AstrBot subagent_orchestrator.router_system_prompt（astr_main_agent.py:656）",
        "default": "",
    },
    "extra_prompt_text": {
        "type": "textarea", "label": "Workspace Extra Prompt（可选）",
        "description": "对齐 AstrBot 的 EXTRA_PROMPT.md 段；本框架无 workspace，直接把文本填这里",
        "default": "",
    },
    "extra_prompt_path": {
        "type": "string", "label": "Workspace Extra Prompt 的来源路径展示",
        "description": "仅用于段内 `From \\`{path}\\`:` 一行的展示",
        "default": "EXTRA_PROMPT.md",
    },
    "local_mode_prompt": {
        "type": "textarea", "label": "local/沙箱模式段（可选）",
        "description": "对齐 AstrBot computer_use_runtime=local 时追加的段；本框架默认留空",
        "default": "",
    },
    "live_mode": {
        "type": "boolean", "label": "实时通话（live）模式段",
        "description": "追加 AstrBot 原文的 LIVE_MODE_SYSTEM_PROMPT",
        "default": False,
    },
    "web_search_citation_prompt": {
        "type": "textarea", "label": "联网引用段（可选）",
        "description": "对齐 AstrBot WEB_SEARCH_CITATION_PROMPT 的位置；本框架默认留空",
        "default": "",
    },
    # ── 用户消息 ──────────────────────────────────────────
    "prompt_prefix": {
        "type": "string", "label": "用户消息前缀模板",
        "description": '对齐 AstrBot provider_settings.prompt_prefix：含 {{prompt}} 时替换，否则当前缀拼接；默认 "{{prompt}}" 即原样',
        "default": "{{prompt}}",
    },
    "identifier": {
        "type": "boolean", "label": "提醒：User ID / Nickname",
        "description": "对齐 AstrBot provider_settings.identifier（`<system_reminder>` 首行）",
        "default": False,
    },
    "group_name_display": {
        "type": "boolean", "label": "提醒：群名",
        "description": "对齐 AstrBot provider_settings.group_name_display（群名由 pre_request 钩子预取并缓存 30 分钟）",
        "default": False,
    },
    "datetime_system_prompt": {
        "type": "boolean", "label": "提醒：当前时间 / 星期",
        "description": "对齐 AstrBot provider_settings.datetime_system_prompt",
        "default": True,
    },
    "include_quoted_message": {
        "type": "boolean", "label": "引用消息块",
        "description": "对齐 AstrBot 的 `<Quoted Message>` 块（取框架 enhance 预取的 quote 信息）",
        "default": True,
    },
    "include_group_history": {
        "type": "boolean", "label": "群聊上下文块",
        "description": "对齐 AstrBot builtin star 的 group_chat_context 块（含原文头尾与 `[昵称/时间]: ` 行格式）",
        "default": True,
    },
    "group_history_max_messages": {
        "type": "number", "label": "群聊上下文最多几条", "description": "从最近的历史里取多少条用户消息",
        "default": 50, "min": 1, "max": 500,
    },
    "media_placeholder": {
        "type": "boolean", "label": "媒体文本占位",
        "description": "对齐 AstrBot 的 `[Image Attachment: path ...]` / `[File Attachment: ...]`",
        "default": True,
    },
    # ── 历史 ──────────────────────────────────────────────
    "history_mode": {
        "type": "select", "label": "历史形态",
        "description": "raw=AstrBot 风格（只发 role/content，不带框架元信息）；rendered=框架已渲染格式；off=不带历史",
        "default": "raw",
        "options": {
            "raw": "AstrBot 风格（只有 role/content）",
            "rendered": "框架渲染格式（含发送者/时间元信息）",
            "off": "不带历史",
        },
    },
}
