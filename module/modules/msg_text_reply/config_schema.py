"""模块配置 Schema。"""

SCHEMA = {
    "textlable1": {
        "type": "text",
        "label": "配置项示例",
        "description": (
            "text area :\n"
            '{ "text" : "string" }\n'
            '{ "at" : "mode or id" }     all , target , self\n'
            '{ "and" : [ { textarea } , { textarea } ] }\n'
            '{ "or" : [ { textarea } , { textarea } ] }\n'
            "actions :\n  reply\n  emoji\n  poke\n"
            "values:\n  text : string\n  reply : bool\n  at : \"sender\" , \"target\""
        ),
    },
    "customtext": {
        "type": "textarea",
        "label": "自定义配置项",
        "description": "模块使用的自定义配置项模板格式",
        "default": "textarea",
        "placeholder": "输入配置项内容",
        "rows": 10,
    },
}
