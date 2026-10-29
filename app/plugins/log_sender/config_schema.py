"""日志发送模块配置表单。"""

SCHEMA = {
    "enable": {
        "type": "boolean",
        "label": "启用插件",
        "description": "关闭后 /log、/logs 不再响应",
        "default": True,
    },
    "tail_lines": {
        "type": "number",
        "label": "只发末尾行数",
        "description": "0 = 发全量；默认 2000 行，避免把几十 MB 的 debug.log 整包上传",
        "default": 2000,
        "min": 0,
        "step": 100,
    },
    "max_file_mb": {
        "type": "number",
        "label": "单文件大小上限（MB）",
        "description": "超过则只保留末尾内容，并在文件首行注明截断；0 = 不限制",
        "default": 8,
        "min": 0,
        "step": 1,
    },
    "fallback_forward_lines": {
        "type": "number",
        "label": "合并转发里带多少行正文",
        "description": "文件消息发不出去时，每个失败文件在合并转发卡片里带末尾多少行；0 = 只发结果清单",
        "default": 200,
        "min": 0,
        "step": 50,
    },
    "fallback_text_lines": {
        "type": "number",
        "label": "最终兜底纯文本行数",
        "description": "合并转发也发不出去时，在聊天里贴该文件末尾多少行；0 = 不贴",
        "default": 30,
        "min": 0,
        "step": 10,
    },
}
