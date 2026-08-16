"""群申请管理模块配置 Schema（供 WebUI 渲染动态独立配置界面）。"""

SCHEMA = {
    "group_request_configs": {
        "type": "dynamic",
        "label": "群申请处理",
        "description": "选择要编辑的群，并为每个群独立配置入群审核规则",
        "endpoint": "group_request_configs",
        "default": {},
    },
}
