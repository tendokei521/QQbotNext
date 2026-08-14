"""模块配置 Schema。"""

SCHEMA = {
    "priority_groups": {
        "type": "list",
        "label": "优先打卡群列表",
        "description": "勾选优先打卡的群，按拖拽顺序执行；其余群随后打卡",
        "endpoint": "groups",
        "id_field": "group_id",
        "name_field": "group_name",
        "meta_fields": ["member_count"],
        "sortable": True,
        "checkboxes": True,
        "mode_select": True,
        "default": {},
    }
}
