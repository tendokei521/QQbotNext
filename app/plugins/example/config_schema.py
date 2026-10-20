"""模块配置 Schema（供 WebUI 渲染表单）。"""

SCHEMA = {
    "api_key": {
        "type": "string",
        "label": "API 密钥",
        "description": "第三方服务的 API Key",
        "default": "",
        "placeholder": "请输入 API Key",
    },
    "enabled": {
        "type": "boolean",
        "label": "启用功能",
        "default": True,
    },
}
