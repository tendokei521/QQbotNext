"""提示词组装模块的配置 Schema（组装 7 键 + 消息元信息 14 键，共 21 键）。

键名、label、description 与原 `llm_assembly` / `llm_message_meta` 逐字一致。
"""

SCHEMA = {
    # ---------- 组装策略（原 `llm_assembly`） ----------
    "behavior_rules_override": {
        "type": "textarea",
        "label": "覆盖行为规则",
        "description": "留空则用内置规则；填写后整段替换",
        "default": "",
    },
    "participation": {
        "type": "select",
        "label": "参与度",
        "description": "影响行为规则段的语气与主动程度",
        "default": "normal",
        "options": {"low": "低（少插话）", "normal": "正常", "high": "高（多参与）"},
    },
    "emoji_react_level": {
        "type": "number",
        "label": "贴表情活跃度",
        "description": "0 = 几乎不贴；数值越大越爱贴（1 每 3~5 轮、2 优先、3 能贴就贴）。",
        "default": 0,
    },
    "user_message_format": {
        "type": "select",
        "label": "本轮消息格式",
        "description": "用户消息的渲染形态",
        "default": "default",
        "options": {
            "default": "默认（多行）",
            "single_line": "单行",
            "labeled": "带标签",
        },
    },
    "prompt_time_position": {
        "type": "select",
        "label": "时间行位置",
        "description": "当前时间放在用户消息头部还是尾部",
        "default": "user_head",
        "options": {"user_head": "消息头部", "user_tail": "消息尾部", "off": "不注入"},
    },
    "prompt_dump_enable": {
        "type": "boolean",
        "label": "请求落盘（对账）",
        "description": "把每轮最终请求写入文件，便于比对。默认关闭。",
        "default": False,
    },
    "prompt_dump_max_entries": {
        "type": "number",
        "label": "落盘保留条数",
        "description": "滚动文件最多保留多少轮请求（从最旧开始丢）",
        "default": 20,
    },
    # ---------- 消息元信息（原 `llm_message_meta`） ----------
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
