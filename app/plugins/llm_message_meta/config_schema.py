"""消息元信息模块的配置 Schema（原 agent「用户信息感知」组，共 14 键）。"""

SCHEMA = {
    "include_time": {
        "type": "boolean",
        "label": "包含当前时间",
        "description": "在用户消息里带上时间，让模型知道「现在几点」",
        "default": True,
    },
    "include_sender": {
        "type": "boolean",
        "label": "包含发送者信息",
        "description": "带上发送者昵称与 QQ",
        "default": True,
    },
    "include_private_qq": {
        "type": "boolean",
        "label": "私信包含对方QQ",
        "description": "私聊时是否把对方 QQ 作为元信息给出",
        "default": False,
    },
    "include_mentioned": {
        "type": "boolean",
        "label": "包含提到了信息",
        "description": "列出本条消息 @ 了谁（可反查昵称）",
        "default": True,
    },
    "include_quote": {
        "type": "boolean",
        "label": "包含引用消息",
        "description": "本条消息引用了哪条（内容由下面的开关决定是否拉取）",
        "default": True,
    },
    "include_quote_sender": {
        "type": "boolean",
        "label": "引用消息包含发送者",
        "description": "引用行里带被引用消息的发送者",
        "default": True,
    },
    "include_sent": {
        "type": "boolean",
        "label": "包含发送内容",
        "description": "附带本条消息的原始正文（便于模型区分元信息与正文）",
        "default": True,
    },
    "fetch_at_nickname": {
        "type": "boolean",
        "label": "拉取 @ 对象昵称",
        "description": "关掉则只显示 QQ 号（省一次 API 调用）",
        "default": True,
    },
    "fetch_quote_content": {
        "type": "boolean",
        "label": "拉取引用消息内容",
        "description": "关掉则只显示「引用了某条」，不拉正文",
        "default": True,
    },
    "meta_sender_style": {
        "type": "select",
        "label": "发送者写法",
        "description": "元信息里发送者的呈现风格",
        "default": "compact",
        "options": {"compact": "紧凑（昵称(QQ)）", "verbose": "详细", "name_only": "只要昵称"},
    },
    "meta_sent_style": {
        "type": "select",
        "label": "正文写法",
        "description": "元信息里正文的呈现风格",
        "default": "compact",
        "options": {"compact": "紧凑", "quoted": "带引号", "fenced": "代码块"},
    },
    "meta_instruction_mode": {
        "type": "select",
        "label": "消歧说明",
        "description": "是否注入「这些元信息不是用户说的话」的说明",
        "default": "auto",
        "options": {"auto": "自动（有歧义才加）", "always": "总是加", "off": "不加"},
    },
    "meta_mask_nickname": {
        "type": "boolean",
        "label": "昵称脱敏",
        "description": "把句子型昵称替换成安全名（避免模型把昵称当指令）",
        "default": False,
    },
    "clean_output_parentheses": {
        "type": "boolean",
        "label": "写入历史时清洗括号",
        "description": "剥离助手输出里的（…），阻止括号风格自我强化（只影响历史，不影响对外展示）",
        "default": True,
    },
}
