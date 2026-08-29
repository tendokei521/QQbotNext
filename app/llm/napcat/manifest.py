"""NapCat / OneBot API 工具清单。

每条记录：
- name: 对应 OneBot action
- description: 给 LLM 看的说明
- parameters: OpenAI function calling 参数 schema
- risk: read / send / admin
- permission: 默认工具权限
- scopes: group / private / ["*"]
"""

NAP_CAT_TOOLS: list[dict] = [
    # ---------- 只读/状态 ----------
    {
        "name": "get_login_info",
        "description": "获取当前 Bot 登录账号信息。",
        "parameters": {"type": "object", "properties": {}},
        "risk": "read",
        "permission": "member",
        "scopes": ["*"],
    },
    {
        "name": "get_status",
        "description": "获取 Bot 当前运行状态。",
        "parameters": {"type": "object", "properties": {}},
        "risk": "read",
        "permission": "member",
        "scopes": ["*"],
    },
    {
        "name": "get_version_info",
        "description": "获取 NapCat/OneBot 版本信息。",
        "parameters": {"type": "object", "properties": {}},
        "risk": "read",
        "permission": "member",
        "scopes": ["*"],
    },
    {
        "name": "get_group_list",
        "description": "获取当前 Bot 加入的所有群列表。",
        "parameters": {"type": "object", "properties": {}},
        "risk": "read",
        "permission": "member",
        "scopes": ["*"],
    },
    {
        "name": "get_group_info",
        "description": "获取单个群的详细信息。",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {"type": "integer", "description": "群号"},
                "no_cache": {"type": "boolean", "description": "是否关闭缓存"},
            },
            "required": ["group_id"],
        },
        "risk": "read",
        "permission": "member",
        "scopes": ["group", "private"],
    },
    {
        "name": "get_group_member_list",
        "description": "获取群成员列表。",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {"type": "integer", "description": "群号"},
            },
            "required": ["group_id"],
        },
        "risk": "read",
        "permission": "member",
        "scopes": ["group"],
    },
    {
        "name": "get_group_member_info",
        "description": "获取群成员详细信息。",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {"type": "integer"},
                "user_id": {"type": "integer"},
                "no_cache": {"type": "boolean"},
            },
            "required": ["group_id", "user_id"],
        },
        "risk": "read",
        "permission": "member",
        "scopes": ["group"],
    },
    {
        "name": "get_friend_list",
        "description": "获取当前 Bot 好友列表。",
        "parameters": {"type": "object", "properties": {}},
        "risk": "read",
        "permission": "member",
        "scopes": ["private", "*"],
    },
    {
        "name": "get_stranger_info",
        "description": "获取陌生人/用户基本信息。",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "integer"},
                "no_cache": {"type": "boolean"},
            },
            "required": ["user_id"],
        },
        "risk": "read",
        "permission": "member",
        "scopes": ["*"],
    },
    {
        "name": "get_msg_history",
        "description": "获取群聊或私聊历史消息。",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {"type": "integer"},
                "user_id": {"type": "integer"},
                "count": {"type": "integer", "description": "获取条数"},
                "reverse_order": {"type": "boolean"},
            },
            "required": ["count"],
        },
        "risk": "read",
        "permission": "member",
        "scopes": ["group", "private"],
    },
    {
        "name": "get_msg",
        "description": "获取单条消息详情。",
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {"type": "integer"},
            },
            "required": ["message_id"],
        },
        "risk": "read",
        "permission": "member",
        "scopes": ["*"],
    },
    {
        "name": "get_essence_msg_list",
        "description": "获取群置顶消息列表。",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {"type": "integer"},
            },
            "required": ["group_id"],
        },
        "risk": "read",
        "permission": "member",
        "scopes": ["group"],
    },
    # ---------- 消息/互动 ----------
    {
        "name": "send_private_msg",
        "description": "向指定用户发送私聊消息。",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "integer"},
                "message": {"type": "string"},
                "auto_escape": {"type": "boolean"},
            },
            "required": ["user_id", "message"],
        },
        "risk": "send",
        "permission": "member",
        "scopes": ["private"],
    },
    {
        "name": "send_group_msg",
        "description": "向指定群发送群消息。",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {"type": "integer"},
                "message": {"type": "string"},
                "auto_escape": {"type": "boolean"},
            },
            "required": ["group_id", "message"],
        },
        "risk": "send",
        "permission": "member",
        "scopes": ["group"],
    },
    {
        "name": "send_poke",
        "description": "发送戳一戳。",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "integer"},
                "group_id": {"type": "integer"},
                "target_id": {"type": "integer"},
            },
            "required": ["user_id"],
        },
        "risk": "send",
        "permission": "member",
        "scopes": ["group", "private"],
    },
    {
        "name": "send_like",
        "description": "给用户点赞。",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "integer"},
                "times": {"type": "integer"},
            },
            "required": ["user_id"],
        },
        "risk": "send",
        "permission": "member",
        "scopes": ["*"],
    },
    {
        "name": "send_group_sign",
        "description": "群签到。",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {"type": "integer"},
            },
            "required": ["group_id"],
        },
        "risk": "send",
        "permission": "member",
        "scopes": ["group"],
    },
    {
        "name": "set_msg_emoji_like",
        "description": "给消息添加表情回应。",
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {"type": "integer"},
                "emoji_id": {"type": "string"},
            },
            "required": ["message_id", "emoji_id"],
        },
        "risk": "send",
        "permission": "member",
        "scopes": ["*"],
    },
    # ---------- 消息管理 ----------
    {
        "name": "delete_msg",
        "description": "撤回一条消息。",
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {"type": "integer"},
            },
            "required": ["message_id"],
        },
        "risk": "admin",
        "permission": "group_admin",
        "scopes": ["group", "private"],
    },
    {
        "name": "set_essence_msg",
        "description": "把消息设为置顶/精华。",
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {"type": "integer"},
            },
            "required": ["message_id"],
        },
        "risk": "admin",
        "permission": "group_admin",
        "scopes": ["group"],
    },
    {
        "name": "delete_essence_msg",
        "description": "取消消息置顶/精华。",
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {"type": "integer"},
            },
            "required": ["message_id"],
        },
        "risk": "admin",
        "permission": "group_admin",
        "scopes": ["group"],
    },
    # ---------- 群管理 ----------
    {
        "name": "set_group_card",
        "description": "设置群成员群名片。",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {"type": "integer"},
                "user_id": {"type": "integer"},
                "card": {"type": "string"},
            },
            "required": ["group_id", "user_id"],
        },
        "risk": "admin",
        "permission": "group_admin",
        "scopes": ["group"],
    },
    {
        "name": "set_group_ban",
        "description": "禁言群成员。",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {"type": "integer"},
                "user_id": {"type": "integer"},
                "duration": {"type": "integer", "description": "禁言秒数，默认1800"},
            },
            "required": ["group_id", "user_id"],
        },
        "risk": "admin",
        "permission": "group_admin",
        "scopes": ["group"],
    },
    {
        "name": "set_group_whole_ban",
        "description": "开启/关闭全员禁言。",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {"type": "integer"},
                "enable": {"type": "boolean"},
            },
            "required": ["group_id", "enable"],
        },
        "risk": "admin",
        "permission": "group_owner",
        "scopes": ["group"],
    },
    {
        "name": "set_group_kick",
        "description": "踢出群成员。",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {"type": "integer"},
                "user_id": {"type": "integer"},
                "reject_add_request": {"type": "boolean"},
            },
            "required": ["group_id", "user_id"],
        },
        "risk": "admin",
        "permission": "group_owner",
        "scopes": ["group"],
    },
    {
        "name": "set_group_admin",
        "description": "设置/取消群管理员。",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {"type": "integer"},
                "user_id": {"type": "integer"},
                "enable": {"type": "boolean"},
            },
            "required": ["group_id", "user_id", "enable"],
        },
        "risk": "admin",
        "permission": "group_owner",
        "scopes": ["group"],
    },
    {
        "name": "set_group_name",
        "description": "修改群名称。",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {"type": "integer"},
                "group_name": {"type": "string"},
            },
            "required": ["group_id", "group_name"],
        },
        "risk": "admin",
        "permission": "group_owner",
        "scopes": ["group"],
    },
    {
        "name": "set_group_leave",
        "description": "退出群聊（可选解散）。",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {"type": "integer"},
                "is_dismiss": {"type": "boolean"},
            },
            "required": ["group_id"],
        },
        "risk": "admin",
        "permission": "owner",
        "scopes": ["group"],
    },
    # ---------- 申请处理 ----------
    {
        "name": "set_group_add_request",
        "description": "处理加群申请。",
        "parameters": {
            "type": "object",
            "properties": {
                "flag": {"type": "string"},
                "approve": {"type": "boolean"},
                "reason": {"type": "string"},
            },
            "required": ["flag", "approve"],
        },
        "risk": "admin",
        "permission": "group_admin",
        "scopes": ["group"],
    },
    {
        "name": "set_friend_add_request",
        "description": "处理好友申请。",
        "parameters": {
            "type": "object",
            "properties": {
                "flag": {"type": "string"},
                "approve": {"type": "boolean"},
                "remark": {"type": "string"},
            },
            "required": ["flag", "approve"],
        },
        "risk": "admin",
        "permission": "owner",
        "scopes": ["private"],
    },
    # ---------- 资源/能力 ----------
    {
        "name": "can_send_image",
        "description": "检查当前账号能否发送图片。",
        "parameters": {"type": "object", "properties": {}},
        "risk": "read",
        "permission": "member",
        "scopes": ["*"],
    },
    {
        "name": "can_send_record",
        "description": "检查当前账号能否发送语音。",
        "parameters": {"type": "object", "properties": {}},
        "risk": "read",
        "permission": "member",
        "scopes": ["*"],
    },
    {
        "name": "clean_cache",
        "description": "清理 NapCat/OneBot 缓存。",
        "parameters": {"type": "object", "properties": {}},
        "risk": "admin",
        "permission": "owner",
        "scopes": ["*"],
    },
]
