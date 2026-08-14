"""模块配置 Schema。"""

SCHEMA = {
    "cache_time": {
        "type": "number",
        "label": "缓存时间",
        "description": "缓存时间，单位秒",
        "default": 600,
        "placeholder": "输入缓存时间，单位秒",
    },
    "target": {
        "type": "select",
        "label": "转发范围",
        "description": "选择模块的转发范围",
        "default": "auto",
        "options": {
            "default": "仅保存在本地",
            "group": "发送到群聊",
            "private": "发送到私信",
            "all": "发送到所有范围",
        },
    },
    "target_groups": {
        "type": "list",
        "label": "目标群聊",
        "description": "撤回消息转发到的群（从群列表勾选）",
        "endpoint": "groups",
        "id_field": "group_id",
        "name_field": "group_name",
        "meta_fields": ["member_count"],
        "checkboxes": True,
        "mode_select": True,
        "default": {},
    },
    "target_users": {
        "type": "list",
        "label": "目标用户",
        "description": "撤回消息转发到的用户（从好友列表勾选）",
        "endpoint": "friends",
        "id_field": "user_id",
        "name_field": "nickname",
        "checkboxes": True,
        "mode_select": True,
        "default": {},
    },
}
