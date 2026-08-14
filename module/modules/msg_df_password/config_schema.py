"""模块配置 Schema。"""

SCHEMA = {
    "group_list": {
        "type": "list",
        "label": "启用自动更新的群",
        "description": "在这些群中自动更新今日密码（从群列表勾选，可拖拽排序）",
        "endpoint": "groups",
        "id_field": "group_id",
        "name_field": "group_name",
        "meta_fields": ["member_count"],
        "sortable": True,
        "checkboxes": True,
        "mode_select": True,
        "default": {},
    },
    "strict_text": {
        "type": "boolean",
        "label": "是否仅匹配#今日密码",
        "description": "",
        "default": True,
    },
    "push_enable": {
        "type": "boolean",
        "label": "更新时推送至精华消息",
        "description": "更新今日密码时推送至群聊精华消息",
        "default": False,
    },
}
