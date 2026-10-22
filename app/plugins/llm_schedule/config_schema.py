"""定时任务模块的配置 Schema（原 agent「定时任务」组，共 3 键）。"""

SCHEMA = {
    "schedule_enable": {
        "type": "boolean",
        "label": "启用定时任务",
        "description": "允许模型/你创建到点触发的任务",
        "default": True,
    },
    "schedule_prompt": {
        "type": "textarea",
        "label": "定时触发提示词",
        "description": "留空用内置模板（让它像真人一样自然开口）",
        "default": "",
    },
    "stream_scheduled_enabled": {
        "type": "boolean",
        "label": "定时任务走流式",
        "description": "复用「输出策略」模块的流式配置",
        "default": False,
    },
}
