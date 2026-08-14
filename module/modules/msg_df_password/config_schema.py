"""模块配置 Schema。"""

SCHEMA = {
    "strict_text": {
        "type": "boolean",
        "label": "仅匹配 #今日密码",
        "description": "开启后消息必须严格等于 #今日密码 才响应",
        "default": True,
    },
    "enable_command": {
        "type": "boolean",
        "label": "启用指令查询",
        "description": "允许通过 #今日密码 指令查询",
        "default": True,
    },
    "cmd_group_mode": {
        "type": "select",
        "label": "指令生效群范围",
        "description": "在哪些群中响应 #今日密码",
        "default": "all",
        "options": {
            "all": "全部群",
            "partial": "仅勾选的群",
            "none": "不在群内响应",
        },
    },
    "command_response_groups": {
        "type": "list",
        "label": "指令生效群列表",
        "description": "勾选响应 #今日密码 的群",
        "endpoint": "groups",
        "id_field": "group_id",
        "name_field": "group_name",
        "meta_fields": ["member_count"],
        "checkboxes": True,
        "default": {},
    },
    "enable_cron": {
        "type": "boolean",
        "label": "启用定时推送",
        "description": "每天在设定时间自动推送今日密码",
        "default": False,
    },
    "cron_time": {
        "type": "time",
        "label": "推送时间",
        "description": "每日自动推送触发时间（修改后需刷新模块生效）",
        "default": "08:00",
    },
    "cron_group_mode": {
        "type": "select",
        "label": "推送群范围",
        "description": "定时推送覆盖的群范围",
        "default": "all",
        "options": {
            "all": "全部群",
            "partial": "仅勾选的群",
            "none": "不推送",
        },
    },
    "cron_send_groups": {
        "type": "list",
        "label": "推送群列表",
        "description": "勾选接收定时推送的群",
        "endpoint": "groups",
        "id_field": "group_id",
        "name_field": "group_name",
        "meta_fields": ["member_count"],
        "checkboxes": True,
        "default": {},
    },
    "push_enable": {
        "type": "boolean",
        "label": "更新时推送至精华消息",
        "description": "更新今日密码时推送至群聊精华消息",
        "default": False,
    },
    "default_site": {
        "type": "select",
        "label": "主数据源",
        "description": "优先从哪个站点获取密码（kkrb 即时更新）",
        "default": "kkrb",
        "options": {
            "tmini": "tmini.net（GET 直取）",
            "kkrb": "kkrb.net（POST 三步）",
        },
    },
    "enable_fallback": {
        "type": "boolean",
        "label": "启用备用源",
        "description": "主数据源失败时自动尝试备用源",
        "default": True,
    },
    "fallback_site": {
        "type": "select",
        "label": "备用数据源",
        "description": "主数据源失败时的备用站点",
        "default": "tmini",
        "options": {
            "tmini": "tmini.net（GET 直取）",
            "kkrb": "kkrb.net（POST 三步）",
        },
    },
}
