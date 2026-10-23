"""AstrBot 组装对齐插件的配置表单（WebUI）。

默认值必须与 `main.py` 的 `DEFAULT_CONFIG` 一致（有测试卡这条）。
"""

from __future__ import annotations

SCHEMA: dict = {
    # ── 人设 ──────────────────────────────────────────────
    "persona_preset_id": {
        "type": "resource", "resource": "role_preset",
        "label": "角色预设（可选）",
        "description": "选系统角色预设；留空则用 Agent 页的系统提示词。",
        "default": "",
    },
    "persona_begin_dialogs": {
        "type": "boolean", "label": "把预设对话前插到历史最前",
        "description": "角色预设的开场白，按顺序注入。",
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
        "description": "只在挂载工具时注入；可选完整版或精简版。",
        "default": "full",
        "options": {"full": "完整版（full）", "skills_like": "skills-like 版", "off": "不注入"},
    },
    "router_system_prompt": {
        "type": "string", "label": "Subagent router 段（可选）",
        "description": "子任务分派时使用的系统提示词。",
        "default": "",
    },
    "extra_prompt_text": {
        "type": "textarea", "label": "Workspace Extra Prompt（可选）",
        "description": "追加在工作区说明之后的补充提示，直接写正文即可。",
        "default": "",
    },
    "extra_prompt_path": {
        "type": "string", "label": "Workspace Extra Prompt 的来源路径展示",
        "description": "仅用于段内 `From \\`{path}\\`:` 一行的展示",
        "default": "EXTRA_PROMPT.md",
    },
    "local_mode_prompt": {
        "type": "textarea", "label": "local/沙箱模式段（可选）",
        "description": "本地执行模式追加的说明，默认留空。",
        "default": "",
    },
    "live_mode": {
        "type": "boolean", "label": "实时通话（live）模式段",
        "description": "追加 AstrBot 原文的 LIVE_MODE_SYSTEM_PROMPT",
        "default": False,
    },
    "web_search_citation_prompt": {
        "type": "textarea", "label": "联网引用段（可选）",
        "description": "联网搜索结果的引用说明，留空使用内置文案。",
        "default": "",
    },
    # ── 用户消息 ──────────────────────────────────────────
    "prompt_prefix": {
        "type": "string", "label": "用户消息前缀模板",
        "description": "最终提示的前缀模板，`{{prompt}}` 指代原提示。",
        "default": "{{prompt}}",
    },
    "identifier": {
        "type": "boolean", "label": "提醒：User ID / Nickname",
        "description": "注入的身份标识首行文本。",
        "default": False,
    },
    "group_name_display": {
        "type": "boolean", "label": "提醒：群名",
        "description": "在提示中显示群名，预取后缓存 30 分钟。",
        "default": False,
    },
    "datetime_system_prompt": {
        "type": "boolean", "label": "提醒：当前时间 / 星期",
        "description": "在提示中注入当前时间。",
        "default": True,
    },
    "include_quoted_message": {
        "type": "boolean", "label": "引用消息块",
        "description": "把被引用的原消息作为独立块注入。",
        "default": True,
    },
    "include_group_history": {
        "type": "boolean", "label": "群聊上下文块",
        "description": "把最近群聊记录作为背景块注入。",
        "default": True,
    },
    "group_history_max_messages": {
        "type": "number", "label": "群聊上下文最多几条", "description": "从最近的历史里取多少条用户消息",
        "default": 50, "min": 1, "max": 500,
    },
    "media_placeholder": {
        "type": "boolean", "label": "媒体文本占位",
        "description": "图片与文件的占位标记文本。",
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
