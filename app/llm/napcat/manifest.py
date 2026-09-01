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
        "category": "系统接口",
        "doc_url": "https://napcat.apifox.cn/226656952e0",
    },
    {
        "name": "get_status",
        "description": "获取 Bot 当前运行状态。",
        "parameters": {"type": "object", "properties": {}},
        "risk": "read",
        "permission": "member",
        "scopes": ["*"],
        "category": "系统接口",
        "doc_url": "https://napcat.apifox.cn/226657083e0",
    },
    {
        "name": "get_version_info",
        "description": "获取 NapCat/OneBot 版本信息。",
        "parameters": {"type": "object", "properties": {}},
        "risk": "read",
        "permission": "member",
        "scopes": ["*"],
        "category": "系统接口",
        "doc_url": "https://napcat.apifox.cn/226657087e0",
    },
    {
        "name": "get_group_list",
        "description": "获取当前 Bot 加入的所有群列表。",
        "parameters": {"type": "object", "properties": {}},
        "risk": "read",
        "permission": "member",
        "scopes": ["*"],
        "category": "群组接口",
        "doc_url": "https://napcat.apifox.cn/226656992e0",
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
        "category": "群组接口",
        "doc_url": "https://napcat.apifox.cn/226656979e0",
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
        "category": "群组接口",
        "doc_url": "https://napcat.apifox.cn/226657034e0",
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
        "category": "群组接口",
        "doc_url": "https://napcat.apifox.cn/226657019e0",
    },
    {
        "name": "get_friend_list",
        "description": "获取当前 Bot 好友列表。",
        "parameters": {"type": "object", "properties": {}},
        "risk": "read",
        "permission": "member",
        "scopes": ["private", "*"],
        "category": "用户接口",
        "doc_url": "https://napcat.apifox.cn/226656976e0",
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
        "category": "Go-CQHTTP",
        "doc_url": "https://napcat.apifox.cn/226656970e0",
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
        "category": "消息接口",
        "doc_url": "https://napcat.apifox.cn/",
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
        "category": "消息接口",
        "doc_url": "https://napcat.apifox.cn/226656707e0",
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
        "category": "群组接口",
        "doc_url": "https://napcat.apifox.cn/226658664e0",
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
        "category": "消息接口",
        "doc_url": "https://napcat.apifox.cn/226656553e0",
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
        "category": "群组接口",
        "doc_url": "https://napcat.apifox.cn/226656598e0",
    },
    {
        "name": "send_poke",
        "description": "发送戳一戳。群聊中必须填写当前群号 group_id，否则会变成私聊戳一戳；私聊只需填写 user_id。",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "integer", "description": "被戳的用户 QQ 号"},
                "group_id": {"type": "integer", "description": "群号；群聊中戳一戳必填，应填写当前群号"},
                "target_id": {"type": "integer", "description": "目标 QQ 号（NapCat 扩展字段），通常可省略"},
            },
            "required": ["user_id"],
        },
        "risk": "send",
        "permission": "member",
        "scopes": ["group", "private"],
        "category": "核心接口",
        "doc_url": "https://napcat.apifox.cn/250286923e0",
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
        "category": "用户接口",
        "doc_url": "https://napcat.apifox.cn/226656717e0",
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
        "category": "群组扩展",
        "doc_url": "https://napcat.apifox.cn/230897177e0",
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
        "category": "消息扩展",
        "doc_url": "https://napcat.apifox.cn/226659104e0",
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
        "category": "消息接口",
        "doc_url": "https://napcat.apifox.cn/226919954e0",
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
        "category": "群组接口",
        "doc_url": "https://napcat.apifox.cn/226658674e0",
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
        "category": "群组接口",
        "doc_url": "https://napcat.apifox.cn/226658678e0",
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
        "category": "群组接口",
        "doc_url": "https://napcat.apifox.cn/226656913e0",
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
        "category": "群组接口",
        "doc_url": "https://napcat.apifox.cn/226656791e0",
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
        "category": "群组接口",
        "doc_url": "https://napcat.apifox.cn/226656802e0",
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
        "category": "群组接口",
        "doc_url": "https://napcat.apifox.cn/226656748e0",
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
        "category": "群组接口",
        "doc_url": "https://napcat.apifox.cn/226656815e0",
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
        "category": "群组接口",
        "doc_url": "https://napcat.apifox.cn/226656919e0",
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
        "category": "群组接口",
        "doc_url": "https://napcat.apifox.cn/226656926e0",
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
        "category": "群组接口",
        "doc_url": "https://napcat.apifox.cn/226656947e0",
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
        "category": "用户接口",
        "doc_url": "https://napcat.apifox.cn/226656932e0",
    },
    # ---------- 资源/能力 ----------
    {
        "name": "can_send_image",
        "description": "检查当前账号能否发送图片。",
        "parameters": {"type": "object", "properties": {}},
        "risk": "read",
        "permission": "member",
        "scopes": ["*"],
        "category": "系统接口",
        "doc_url": "https://napcat.apifox.cn/226657071e0",
    },
    {
        "name": "can_send_record",
        "description": "检查当前账号能否发送语音。",
        "parameters": {"type": "object", "properties": {}},
        "risk": "read",
        "permission": "member",
        "scopes": ["*"],
        "category": "系统接口",
        "doc_url": "https://napcat.apifox.cn/226657080e0",
    },
    {
        "name": "clean_cache",
        "description": "清理 NapCat/OneBot 缓存。",
        "parameters": {"type": "object", "properties": {}},
        "risk": "admin",
        "permission": "owner",
        "scopes": ["*"],
        "category": "系统接口",
        "doc_url": "https://napcat.apifox.cn/298305106e0",
    },
    {
        "name": ".handle_quick_operation",
        "description": "处理来自事件上报的快速操作请求",
        "parameters": {
            "type": "object",
            "properties": {
                "context": {
                    "type": "object",
                    "description": "事件上下文"
                },
                "operation": {
                    "type": "object",
                    "description": "快速操作内容"
                }
            },
            "required": [
                "context",
                "operation"
            ]
        },
        "risk": "admin",
        "permission": "owner",
        "scopes": [
            "*"
        ],
        "category": "Go-CQHTTP",
        "doc_url": "https://napcat.apifox.cn/226658889e0",
    },
    {
        "name": ".ocr_image",
        "description": "识别图片中的文字内容(仅Windows端支持)",
        "parameters": {
            "type": "object",
            "properties": {
                "image": {
                    "type": "string",
                    "description": "图片路径、URL或Base64"
                }
            },
            "required": [
                "image"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "扩展接口",
        "doc_url": "https://napcat.apifox.cn/226658234e0",
    },
    {
        "name": "ArkShareGroup",
        "description": "获取群分享的 Ark 内容",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                }
            },
            "required": [
                "group_id"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "group"
        ],
        "category": "消息扩展",
        "doc_url": "https://napcat.apifox.cn/226658971e0",
    },
    {
        "name": "ArkSharePeer",
        "description": "获取用户推荐的 Ark 内容",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "QQ号"
                },
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "phone_number": {
                    "type": "string",
                    "description": "手机号"
                }
            },
            "required": [
                "phone_number"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "private"
        ],
        "category": "消息扩展",
        "doc_url": "https://napcat.apifox.cn/226658965e0",
    },
    {
        "name": "_del_group_notice",
        "description": "删除群聊中的公告",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "notice_id": {
                    "type": "string",
                    "description": "公告ID"
                }
            },
            "required": [
                "group_id",
                "notice_id"
            ]
        },
        "risk": "admin",
        "permission": "group_owner",
        "scopes": [
            "group"
        ],
        "category": "群组接口",
        "doc_url": "https://napcat.apifox.cn/226659240e0",
    },
    {
        "name": "_get_group_notice",
        "description": "获取指定群聊中的公告列表",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                }
            },
            "required": [
                "group_id"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "group"
        ],
        "category": "群组接口",
        "doc_url": "https://napcat.apifox.cn/226658742e0",
    },
    {
        "name": "_get_model_show",
        "description": "获取当前账号可用的设备机型显示名称列表",
        "parameters": {
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "模型名称"
                }
            },
            "required": []
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "Go-CQHTTP",
        "doc_url": "https://napcat.apifox.cn/227233981e0",
    },
    {
        "name": "_mark_all_as_read",
        "description": "标记所有消息已读",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        },
        "risk": "send",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "消息接口",
        "doc_url": "https://napcat.apifox.cn/226659194e0",
    },
    {
        "name": "_send_group_notice",
        "description": "在指定群聊中发布新的公告",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "content": {
                    "type": "string",
                    "description": "公告内容"
                },
                "image": {
                    "type": "string",
                    "description": "公告图片路径或 URL"
                },
                "pinned": {
                    "type": "number",
                    "description": "是否置顶 (0/1)"
                },
                "type": {
                    "type": "number",
                    "description": "类型 (默认为 1)"
                },
                "confirm_required": {
                    "type": "number",
                    "description": "是否需要确认 (0/1)"
                },
                "is_show_edit_card": {
                    "type": "number",
                    "description": "是否显示修改群名片引导 (0/1)"
                },
                "tip_window_type": {
                    "type": "number",
                    "description": "弹窗类型 (默认为 0)"
                }
            },
            "required": [
                "group_id",
                "content",
                "pinned",
                "type",
                "confirm_required",
                "is_show_edit_card",
                "tip_window_type"
            ]
        },
        "risk": "admin",
        "permission": "group_owner",
        "scopes": [
            "group"
        ],
        "category": "Go-CQHTTP",
        "doc_url": "https://napcat.apifox.cn/226658740e0",
    },
    {
        "name": "_set_model_show",
        "description": "设置当前账号的设备机型名称",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        },
        "risk": "admin",
        "permission": "owner",
        "scopes": [
            "*"
        ],
        "category": "Go-CQHTTP",
        "doc_url": "https://napcat.apifox.cn/227233993e0",
    },
    {
        "name": "add_custom_face",
        "description": "添加自定义表情",
        "parameters": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "本地表情文件路径"
                },
                "emoji_id": {
                    "type": "string",
                    "description": "表情ID，未提供时传空字符串"
                },
                "package_id": {
                    "type": "string",
                    "description": "表情包ID，未提供时传0"
                },
                "file_name": {
                    "type": "string",
                    "description": "文件名，未提供时从file路径取basename"
                },
                "file_size": {
                    "type": "string",
                    "description": "文件大小，未提供时读取本地文件"
                },
                "md5": {
                    "type": "string",
                    "description": "文件MD5，未提供时读取本地文件计算"
                },
                "is_mark_face": {
                    "type": "boolean",
                    "description": "是否商城表情"
                },
                "is_origin": {
                    "type": "boolean",
                    "description": "是否原图"
                }
            },
            "required": [
                "file"
            ]
        },
        "risk": "send",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "系统扩展",
        "doc_url": "https://napcat.apifox.cn/467693193e0",
    },
    {
        "name": "bot_exit",
        "description": "退出登录",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        },
        "risk": "admin",
        "permission": "owner",
        "scopes": [
            "*"
        ],
        "category": "系统扩展",
        "doc_url": "https://napcat.apifox.cn/283136399e0",
    },
    {
        "name": "cancel_group_album_media_like",
        "description": "取消点赞群相册媒体",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "album_id": {
                    "type": "string",
                    "description": "相册ID"
                },
                "batch_id": {
                    "type": "string",
                    "description": "batch_id"
                },
                "lloc": {
                    "type": "string",
                    "description": "lloc，若对整个上传操作则不填"
                }
            },
            "required": [
                "group_id",
                "album_id",
                "batch_id"
            ]
        },
        "risk": "send",
        "permission": "group_owner",
        "scopes": [
            "group"
        ],
        "category": "群组扩展",
        "doc_url": "https://napcat.apifox.cn/462330906e0",
    },
    {
        "name": "cancel_group_todo",
        "description": "将指定消息对应的群待办取消",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "message_id": {
                    "type": "string",
                    "description": "消息ID"
                },
                "message_seq": {
                    "type": "string",
                    "description": "消息Seq (可选)"
                }
            },
            "required": [
                "group_id"
            ]
        },
        "risk": "send",
        "permission": "group_admin",
        "scopes": [
            "group"
        ],
        "category": "核心接口",
        "doc_url": "https://napcat.apifox.cn/444247698e0",
    },
    {
        "name": "cancel_online_file",
        "description": "取消在线文件",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "用户 QQ"
                },
                "msg_id": {
                    "type": "string",
                    "description": "消息 ID"
                }
            },
            "required": [
                "user_id",
                "msg_id"
            ]
        },
        "risk": "admin",
        "permission": "owner",
        "scopes": [
            "private"
        ],
        "category": "文件扩展",
        "doc_url": "https://napcat.apifox.cn/410334677e0",
    },
    {
        "name": "check_url_safely",
        "description": "检查指定URL的安全等级",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "要检查的 URL"
                }
            },
            "required": [
                "url"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "Go-CQHTTP",
        "doc_url": "https://napcat.apifox.cn/228534361e0",
    },
    {
        "name": "clean_stream_temp_file",
        "description": "清理流式传输临时文件",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        },
        "risk": "admin",
        "permission": "owner",
        "scopes": [
            "*"
        ],
        "category": "流式传输扩展",
        "doc_url": "https://napcat.apifox.cn/395354124e0",
    },
    {
        "name": "click_inline_keyboard_button",
        "description": "点击内联键盘按钮",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "bot_appid": {
                    "type": "string",
                    "description": "机器人AppID"
                },
                "button_id": {
                    "type": "string",
                    "description": "按钮ID"
                },
                "callback_data": {
                    "type": "string",
                    "description": "回调数据"
                },
                "msg_seq": {
                    "type": "string",
                    "description": "消息序列号"
                }
            },
            "required": [
                "group_id",
                "bot_appid",
                "button_id",
                "callback_data",
                "msg_seq"
            ]
        },
        "risk": "send",
        "permission": "member",
        "scopes": [
            "group"
        ],
        "category": "消息扩展",
        "doc_url": "https://napcat.apifox.cn/266151864e0",
    },
    {
        "name": "complete_group_todo",
        "description": "将指定消息对应的群待办标记为已完成",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "message_id": {
                    "type": "string",
                    "description": "消息ID"
                },
                "message_seq": {
                    "type": "string",
                    "description": "消息Seq (可选)"
                }
            },
            "required": [
                "group_id"
            ]
        },
        "risk": "send",
        "permission": "group_admin",
        "scopes": [
            "group"
        ],
        "category": "核心接口",
        "doc_url": "https://napcat.apifox.cn/444247697e0",
    },
    {
        "name": "create_collection",
        "description": "创建收藏",
        "parameters": {
            "type": "object",
            "properties": {
                "rawData": {
                    "type": "string",
                    "description": "原始数据"
                },
                "brief": {
                    "type": "string",
                    "description": "简要描述"
                }
            },
            "required": [
                "rawData",
                "brief"
            ]
        },
        "risk": "send",
        "permission": "owner",
        "scopes": [
            "*"
        ],
        "category": "扩展接口",
        "doc_url": "https://napcat.apifox.cn/226659178e0",
    },
    {
        "name": "create_flash_task",
        "description": "创建闪传任务",
        "parameters": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "description": "文件列表或单个文件路径"
                },
                "name": {
                    "type": "string",
                    "description": "任务名称"
                },
                "thumb_path": {
                    "type": "string",
                    "description": "缩略图路径"
                }
            },
            "required": [
                "files"
            ]
        },
        "risk": "send",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "文件扩展",
        "doc_url": "https://napcat.apifox.cn/410334666e0",
    },
    {
        "name": "create_group_file_folder",
        "description": "在群文件系统中创建新的文件夹",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "folder_name": {
                    "type": "string",
                    "description": "文件夹名称"
                },
                "name": {
                    "type": "string",
                    "description": "文件夹名称"
                }
            },
            "required": [
                "group_id"
            ]
        },
        "risk": "admin",
        "permission": "group_owner",
        "scopes": [
            "group"
        ],
        "category": "Go-CQHTTP",
        "doc_url": "https://napcat.apifox.cn/226658773e0",
    },
    {
        "name": "del_group_album_media",
        "description": "删除群相册媒体",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "album_id": {
                    "type": "string",
                    "description": "相册ID"
                },
                "lloc": {
                    "type": "string",
                    "description": "媒体ID (lloc)"
                }
            },
            "required": [
                "group_id",
                "album_id",
                "lloc"
            ]
        },
        "risk": "admin",
        "permission": "group_owner",
        "scopes": [
            "group"
        ],
        "category": "群组扩展",
        "doc_url": "https://napcat.apifox.cn/395455119e0",
    },
    {
        "name": "delete_custom_face",
        "description": "删除自定义表情",
        "parameters": {
            "type": "object",
            "properties": {
                "res_id": {
                    "type": "string",
                    "description": ""
                },
                "id": {
                    "type": "string",
                    "description": ""
                },
                "ids": {
                    "type": "array",
                    "items": {
                        "type": "string"
                    }
                },
                "md5": {
                    "type": "string",
                    "description": ""
                }
            },
            "required": []
        },
        "risk": "admin",
        "permission": "owner",
        "scopes": [
            "*"
        ],
        "category": "系统扩展",
        "doc_url": "https://napcat.apifox.cn/467693194e0",
    },
    {
        "name": "delete_friend",
        "description": "从好友列表中删除指定用户",
        "parameters": {
            "type": "object",
            "properties": {
                "friend_id": {
                    "type": "string",
                    "description": "好友 QQ 号"
                },
                "user_id": {
                    "type": "string",
                    "description": "用户 QQ 号"
                },
                "temp_block": {
                    "type": "boolean",
                    "description": "是否加入黑名单"
                },
                "temp_both_del": {
                    "type": "boolean",
                    "description": "是否双向删除"
                }
            },
            "required": []
        },
        "risk": "admin",
        "permission": "owner",
        "scopes": [
            "private"
        ],
        "category": "Go-CQHTTP",
        "doc_url": "https://napcat.apifox.cn/227237873e0",
    },
    {
        "name": "delete_group_file",
        "description": "在群文件系统中删除指定的文件",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "file_id": {
                    "type": "string",
                    "description": "文件ID"
                }
            },
            "required": [
                "group_id",
                "file_id"
            ]
        },
        "risk": "admin",
        "permission": "group_owner",
        "scopes": [
            "group"
        ],
        "category": "Go-CQHTTP",
        "doc_url": "https://napcat.apifox.cn/226658755e0",
    },
    {
        "name": "delete_group_folder",
        "description": "在群文件系统中删除指定的文件夹",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "folder_id": {
                    "type": "string",
                    "description": "文件夹ID"
                },
                "folder": {
                    "type": "string",
                    "description": "文件夹ID"
                }
            },
            "required": [
                "group_id"
            ]
        },
        "risk": "admin",
        "permission": "group_owner",
        "scopes": [
            "group"
        ],
        "category": "Go-CQHTTP",
        "doc_url": "https://napcat.apifox.cn/226658779e0",
    },
    {
        "name": "delete_qzone_msg",
        "description": "删除QQ空间(Qzone)的一条说说, 按 tid 删除",
        "parameters": {
            "type": "object",
            "properties": {
                "tid": {
                    "type": "string",
                    "description": "说说 tid (来自 get_qzone_msg_list / send_qzone_msg)"
                }
            },
            "required": [
                "tid"
            ]
        },
        "risk": "admin",
        "permission": "owner",
        "scopes": [
            "*"
        ],
        "category": "扩展接口",
        "doc_url": "https://napcat.apifox.cn/496813059e0",
    },
    {
        "name": "do_group_album_comment",
        "description": "发表群相册评论",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "album_id": {
                    "type": "string",
                    "description": "相册 ID"
                },
                "lloc": {
                    "type": "string",
                    "description": "图片 ID"
                },
                "content": {
                    "type": "string",
                    "description": "评论内容"
                }
            },
            "required": [
                "group_id",
                "album_id",
                "lloc",
                "content"
            ]
        },
        "risk": "send",
        "permission": "member",
        "scopes": [
            "group"
        ],
        "category": "群组扩展",
        "doc_url": "https://napcat.apifox.cn/395458911e0",
    },
    {
        "name": "download_file",
        "description": "下载网络文件到本地临时目录",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "下载链接"
                },
                "base64": {
                    "type": "string",
                    "description": "base64数据"
                },
                "name": {
                    "type": "string",
                    "description": "文件名"
                },
                "headers": {
                    "type": "string",
                    "description": "请求头"
                }
            },
            "required": []
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "Go-CQHTTP",
        "doc_url": "https://napcat.apifox.cn/226658887e0",
    },
    {
        "name": "download_file_image_stream",
        "description": "下载图片文件流",
        "parameters": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "文件路径或 URL"
                },
                "file_id": {
                    "type": "string",
                    "description": "文件 ID"
                },
                "chunk_size": {
                    "type": "number",
                    "description": "分块大小 (字节)"
                }
            },
            "required": []
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "流式传输扩展",
        "doc_url": "https://napcat.apifox.cn/395419462e0",
    },
    {
        "name": "download_file_record_stream",
        "description": "下载语音文件流",
        "parameters": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "文件路径或 URL"
                },
                "file_id": {
                    "type": "string",
                    "description": "文件 ID"
                },
                "chunk_size": {
                    "type": "number",
                    "description": "分块大小 (字节)"
                },
                "out_format": {
                    "type": "string",
                    "description": "输出格式"
                }
            },
            "required": []
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "流式传输扩展",
        "doc_url": "https://napcat.apifox.cn/395417040e0",
    },
    {
        "name": "download_file_stream",
        "description": "以流式方式从网络或本地下载文件",
        "parameters": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "文件路径或 URL"
                },
                "file_id": {
                    "type": "string",
                    "description": "文件 ID"
                },
                "chunk_size": {
                    "type": "number",
                    "description": "分块大小 (字节)"
                }
            },
            "required": []
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "流式接口",
        "doc_url": "https://napcat.apifox.cn/395413859e0",
    },
    {
        "name": "download_fileset",
        "description": "下载文件集",
        "parameters": {
            "type": "object",
            "properties": {
                "fileset_id": {
                    "type": "string",
                    "description": "文件集 ID"
                }
            },
            "required": [
                "fileset_id"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "文件扩展",
        "doc_url": "https://napcat.apifox.cn/410334678e0",
    },
    {
        "name": "fetch_custom_face",
        "description": "获取自定义表情",
        "parameters": {
            "type": "object",
            "properties": {
                "count": {
                    "type": "number",
                    "description": "获取数量"
                }
            },
            "required": [
                "count"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "系统扩展",
        "doc_url": "https://napcat.apifox.cn/226659210e0",
    },
    {
        "name": "fetch_custom_face_detail",
        "description": "获取自定义表情详情",
        "parameters": {
            "type": "object",
            "properties": {
                "count": {
                    "type": "number",
                    "description": "获取数量"
                }
            },
            "required": [
                "count"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "系统扩展",
        "doc_url": "https://napcat.apifox.cn/467693192e0",
    },
    {
        "name": "fetch_emoji_like",
        "description": "获取表情点赞详情",
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "number",
                    "description": "消息ID"
                },
                "emojiId": {
                    "type": "number",
                    "description": "表情ID"
                },
                "emojiType": {
                    "type": "number",
                    "description": "表情类型"
                },
                "count": {
                    "type": "number",
                    "description": "获取数量"
                },
                "cookie": {
                    "type": "string",
                    "description": "分页Cookie"
                }
            },
            "required": [
                "message_id",
                "emojiId",
                "emojiType",
                "count",
                "cookie"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "消息扩展",
        "doc_url": "https://napcat.apifox.cn/226659219e0",
    },
    {
        "name": "fetch_ptt_text",
        "description": "获取语音转文字结果",
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "number",
                    "description": "消息ID"
                }
            },
            "required": [
                "message_id"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "消息扩展",
        "doc_url": "https://napcat.apifox.cn/458248103e0",
    },
    {
        "name": "forward_friend_single_msg",
        "description": "转发单条消息",
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "number",
                    "description": "消息ID"
                },
                "group_id": {
                    "type": "string",
                    "description": "目标群号"
                },
                "user_id": {
                    "type": "string",
                    "description": "目标用户QQ"
                }
            },
            "required": [
                "message_id"
            ]
        },
        "risk": "send",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "消息接口",
        "doc_url": "https://napcat.apifox.cn/226659051e0",
    },
    {
        "name": "forward_group_single_msg",
        "description": "转发单条消息",
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "number",
                    "description": "消息ID"
                },
                "group_id": {
                    "type": "string",
                    "description": "目标群号"
                },
                "user_id": {
                    "type": "string",
                    "description": "目标用户QQ"
                }
            },
            "required": [
                "message_id"
            ]
        },
        "risk": "send",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "消息接口",
        "doc_url": "https://napcat.apifox.cn/226659074e0",
    },
    {
        "name": "friend_poke",
        "description": "在群聊或私聊中发送戳一戳动作",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "user_id": {
                    "type": "string",
                    "description": "用户QQ"
                },
                "target_id": {
                    "type": "string",
                    "description": "目标QQ"
                }
            },
            "required": [
                "user_id"
            ]
        },
        "risk": "send",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "核心接口",
        "doc_url": "https://napcat.apifox.cn/226659255e0",
    },
    {
        "name": "get_ai_characters",
        "description": "获取群聊中的AI角色列表",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "chat_type": {
                    "type": "number",
                    "description": "聊天类型"
                }
            },
            "required": [
                "group_id",
                "chat_type"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "group"
        ],
        "category": "扩展接口",
        "doc_url": "https://napcat.apifox.cn/229485683e0",
    },
    {
        "name": "get_ai_record",
        "description": "通过 AI 语音引擎获取指定文本的语音 URL",
        "parameters": {
            "type": "object",
            "properties": {
                "character": {
                    "type": "string",
                    "description": "角色ID"
                },
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "text": {
                    "type": "string",
                    "description": "语音文本内容"
                }
            },
            "required": [
                "character",
                "group_id",
                "text"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "group"
        ],
        "category": "AI 扩展",
        "doc_url": "https://napcat.apifox.cn/229486818e0",
    },
    {
        "name": "get_clientkey",
        "description": "获取当前登录帐号的ClientKey",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        },
        "risk": "read",
        "permission": "owner",
        "scopes": [
            "*"
        ],
        "category": "扩展接口",
        "doc_url": "https://napcat.apifox.cn/250286915e0",
    },
    {
        "name": "get_collection_list",
        "description": "获取收藏列表",
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "分类ID"
                },
                "count": {
                    "type": "string",
                    "description": "获取数量"
                }
            },
            "required": [
                "category",
                "count"
            ]
        },
        "risk": "read",
        "permission": "owner",
        "scopes": [
            "*"
        ],
        "category": "系统扩展",
        "doc_url": "https://napcat.apifox.cn/226659182e0",
    },
    {
        "name": "get_cookies",
        "description": "获取指定域名的 Cookies",
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "需要获取 cookies 的域名"
                }
            },
            "required": [
                "domain"
            ]
        },
        "risk": "read",
        "permission": "owner",
        "scopes": [
            "*"
        ],
        "category": "用户接口",
        "doc_url": "https://napcat.apifox.cn/226657041e0",
    },
    {
        "name": "get_credentials",
        "description": "获取登录凭证",
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "需要获取 cookies 的域名"
                }
            },
            "required": [
                "domain"
            ]
        },
        "risk": "read",
        "permission": "owner",
        "scopes": [
            "*"
        ],
        "category": "系统接口",
        "doc_url": "https://napcat.apifox.cn/226657054e0",
    },
    {
        "name": "get_csrf_token",
        "description": "获取 CSRF Token",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        },
        "risk": "read",
        "permission": "owner",
        "scopes": [
            "*"
        ],
        "category": "系统接口",
        "doc_url": "https://napcat.apifox.cn/226657044e0",
    },
    {
        "name": "get_doubt_friends_add_request",
        "description": "获取系统的可疑好友申请列表",
        "parameters": {
            "type": "object",
            "properties": {
                "count": {
                    "type": "number",
                    "description": "获取数量"
                }
            },
            "required": [
                "count"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "private"
        ],
        "category": "系统接口",
        "doc_url": "https://napcat.apifox.cn/289565516e0",
    },
    {
        "name": "get_emoji_likes",
        "description": "获取消息表情点赞列表",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号，短ID可不传"
                },
                "message_id": {
                    "type": "string",
                    "description": "消息ID，可以传递长ID或短ID"
                },
                "emoji_id": {
                    "type": "string",
                    "description": "表情ID"
                },
                "emoji_type": {
                    "type": "string",
                    "description": "表情类型"
                },
                "count": {
                    "type": "number",
                    "description": "数量，0代表全部"
                }
            },
            "required": [
                "message_id",
                "emoji_id",
                "count"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "group"
        ],
        "category": "消息扩展",
        "doc_url": "https://napcat.apifox.cn/410334663e0",
    },
    {
        "name": "get_file",
        "description": "获取指定文件的详细信息及下载路径",
        "parameters": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "文件路径、URL或Base64"
                },
                "file_id": {
                    "type": "string",
                    "description": "文件ID"
                }
            },
            "required": []
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "文件接口",
        "doc_url": "https://napcat.apifox.cn/226658985e0",
    },
    {
        "name": "get_fileset_id",
        "description": "获取文件集 ID",
        "parameters": {
            "type": "object",
            "properties": {
                "share_code": {
                    "type": "string",
                    "description": "分享码或分享链接"
                }
            },
            "required": [
                "share_code"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "文件扩展",
        "doc_url": "https://napcat.apifox.cn/410334679e0",
    },
    {
        "name": "get_fileset_info",
        "description": "获取文件集信息",
        "parameters": {
            "type": "object",
            "properties": {
                "fileset_id": {
                    "type": "string",
                    "description": "文件集 ID"
                }
            },
            "required": [
                "fileset_id"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "文件扩展",
        "doc_url": "https://napcat.apifox.cn/410334671e0",
    },
    {
        "name": "get_flash_file_list",
        "description": "获取闪传文件列表",
        "parameters": {
            "type": "object",
            "properties": {
                "fileset_id": {
                    "type": "string",
                    "description": "文件集 ID"
                }
            },
            "required": [
                "fileset_id"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "文件扩展",
        "doc_url": "https://napcat.apifox.cn/410334667e0",
    },
    {
        "name": "get_flash_file_url",
        "description": "获取闪传文件链接",
        "parameters": {
            "type": "object",
            "properties": {
                "fileset_id": {
                    "type": "string",
                    "description": "文件集 ID"
                },
                "file_name": {
                    "type": "string",
                    "description": "文件名"
                },
                "file_index": {
                    "type": "number",
                    "description": "文件索引"
                }
            },
            "required": [
                "fileset_id"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "文件扩展",
        "doc_url": "https://napcat.apifox.cn/410334668e0",
    },
    {
        "name": "get_forward_msg",
        "description": "获取合并转发消息的具体内容",
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "消息ID"
                },
                "id": {
                    "type": "string",
                    "description": "消息ID"
                }
            },
            "required": []
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "Go-CQHTTP",
        "doc_url": "https://napcat.apifox.cn/226656712e0",
    },
    {
        "name": "get_friend_msg_history",
        "description": "获取指定好友的历史聊天记录",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "用户QQ"
                },
                "message_seq": {
                    "type": "string",
                    "description": "起始消息序号"
                },
                "count": {
                    "type": "number",
                    "description": "获取消息数量"
                },
                "reverse_order": {
                    "type": "boolean",
                    "description": "是否反向排序"
                },
                "disable_get_url": {
                    "type": "boolean",
                    "description": "是否禁用获取URL"
                },
                "parse_mult_msg": {
                    "type": "boolean",
                    "description": "是否解析合并消息"
                },
                "quick_reply": {
                    "type": "boolean",
                    "description": "是否快速回复"
                },
                "reverseOrder": {
                    "type": "boolean",
                    "description": "是否反向排序(旧版本兼容)"
                }
            },
            "required": [
                "user_id",
                "count",
                "reverse_order",
                "disable_get_url",
                "parse_mult_msg",
                "quick_reply",
                "reverseOrder"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "private"
        ],
        "category": "Go-CQHTTP",
        "doc_url": "https://napcat.apifox.cn/226659174e0",
    },
    {
        "name": "get_friends_with_category",
        "description": "获取带分组的好友列表",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "用户扩展",
        "doc_url": "https://napcat.apifox.cn/226658978e0",
    },
    {
        "name": "get_group_album_media_list",
        "description": "获取群相册媒体列表",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "album_id": {
                    "type": "string",
                    "description": "相册ID"
                },
                "attach_info": {
                    "type": "string",
                    "description": "附加信息（用于分页）"
                }
            },
            "required": [
                "group_id",
                "album_id"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "group"
        ],
        "category": "群组扩展",
        "doc_url": "https://napcat.apifox.cn/395459066e0",
    },
    {
        "name": "get_group_at_all_remain",
        "description": "获取指定群聊中艾特全体成员的剩余次数",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                }
            },
            "required": [
                "group_id"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "group"
        ],
        "category": "Go-CQHTTP",
        "doc_url": "https://napcat.apifox.cn/227245941e0",
    },
    {
        "name": "get_group_detail_info",
        "description": "获取群聊的详细信息，包括成员数、最大成员数等",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                }
            },
            "required": [
                "group_id"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "group"
        ],
        "category": "群组接口",
        "doc_url": "https://napcat.apifox.cn/307180859e0",
    },
    {
        "name": "get_group_file_system_info",
        "description": "获取群聊文件系统的空间及状态信息",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                }
            },
            "required": [
                "group_id"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "group"
        ],
        "category": "Go-CQHTTP",
        "doc_url": "https://napcat.apifox.cn/226658789e0",
    },
    {
        "name": "get_group_file_url",
        "description": "获取指定群文件的下载链接",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "file_id": {
                    "type": "string",
                    "description": "文件ID"
                }
            },
            "required": [
                "group_id",
                "file_id"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "group"
        ],
        "category": "文件接口",
        "doc_url": "https://napcat.apifox.cn/226658867e0",
    },
    {
        "name": "get_group_files_by_folder",
        "description": "获取指定群文件夹下的文件及子文件夹列表",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "folder_id": {
                    "type": "string",
                    "description": "文件夹ID"
                },
                "folder": {
                    "type": "string",
                    "description": "文件夹ID"
                },
                "file_count": {
                    "type": "number",
                    "description": "文件数量"
                }
            },
            "required": [
                "group_id",
                "file_count"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "group"
        ],
        "category": "Go-CQHTTP",
        "doc_url": "https://napcat.apifox.cn/226658865e0",
    },
    {
        "name": "get_group_honor_info",
        "description": "获取指定群聊的荣誉信息，如龙王等",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "type": {
                    "type": "string",
                    "description": "荣誉类型",
                    "enum": [
                        "all",
                        "talkative",
                        "performer",
                        "legend",
                        "strong_newbie",
                        "emotion"
                    ]
                }
            },
            "required": [
                "group_id"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "group"
        ],
        "category": "Go-CQHTTP",
        "doc_url": "https://napcat.apifox.cn/226657036e0",
    },
    {
        "name": "get_group_ignore_add_request",
        "description": "获取群被忽略的加群请求",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "group"
        ],
        "category": "群组接口",
        "doc_url": "https://napcat.apifox.cn/226659234e0",
    },
    {
        "name": "get_group_ignored_notifies",
        "description": "获取被忽略的入群申请和邀请通知",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "group"
        ],
        "category": "群组接口",
        "doc_url": "https://napcat.apifox.cn/226659323e0",
    },
    {
        "name": "get_group_info_ex",
        "description": "获取群详细信息 (扩展)",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                }
            },
            "required": [
                "group_id"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "group"
        ],
        "category": "群组扩展",
        "doc_url": "https://napcat.apifox.cn/226659229e0",
    },
    {
        "name": "get_group_msg_history",
        "description": "获取指定群聊的历史聊天记录",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "message_seq": {
                    "type": "string",
                    "description": "起始消息序号"
                },
                "count": {
                    "type": "number",
                    "description": "获取消息数量"
                },
                "reverse_order": {
                    "type": "boolean",
                    "description": "是否反向排序"
                },
                "disable_get_url": {
                    "type": "boolean",
                    "description": "是否禁用获取URL"
                },
                "parse_mult_msg": {
                    "type": "boolean",
                    "description": "是否解析合并消息"
                },
                "quick_reply": {
                    "type": "boolean",
                    "description": "是否快速回复"
                },
                "reverseOrder": {
                    "type": "boolean",
                    "description": "是否反向排序(旧版本兼容)"
                }
            },
            "required": [
                "group_id",
                "count",
                "reverse_order",
                "disable_get_url",
                "parse_mult_msg",
                "quick_reply",
                "reverseOrder"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "group"
        ],
        "category": "Go-CQHTTP",
        "doc_url": "https://napcat.apifox.cn/226657401e0",
    },
    {
        "name": "get_group_root_files",
        "description": "获取群文件根目录下的所有文件和文件夹",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "file_count": {
                    "type": "number",
                    "description": "文件数量"
                }
            },
            "required": [
                "group_id",
                "file_count"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "group"
        ],
        "category": "Go-CQHTTP",
        "doc_url": "https://napcat.apifox.cn/226658823e0",
    },
    {
        "name": "get_group_shut_list",
        "description": "获取群禁言列表",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                }
            },
            "required": [
                "group_id"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "group"
        ],
        "category": "群组接口",
        "doc_url": "https://napcat.apifox.cn/226659300e0",
    },
    {
        "name": "get_group_signed_list",
        "description": "获取群组今日打卡列表",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                }
            },
            "required": [
                "group_id"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "group"
        ],
        "category": "群组扩展",
        "doc_url": "https://napcat.apifox.cn/467693191e0",
    },
    {
        "name": "get_group_system_msg",
        "description": "获取群系统消息",
        "parameters": {
            "type": "object",
            "properties": {
                "count": {
                    "type": "number",
                    "description": "获取的消息数量"
                }
            },
            "required": [
                "count"
            ]
        },
        "risk": "read",
        "permission": "owner",
        "scopes": [
            "group"
        ],
        "category": "系统接口",
        "doc_url": "https://napcat.apifox.cn/226658660e0",
    },
    {
        "name": "get_guild_list",
        "description": "获取当前帐号已加入的频道列表",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "频道接口",
        "doc_url": "https://napcat.apifox.cn/226659311e0",
    },
    {
        "name": "get_guild_service_profile",
        "description": "获取当前帐号在频道中的个人资料",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "频道接口",
        "doc_url": "https://napcat.apifox.cn/226659317e0",
    },
    {
        "name": "get_image",
        "description": "获取指定图片的信息及路径",
        "parameters": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "文件路径、URL或Base64"
                },
                "file_id": {
                    "type": "string",
                    "description": "文件ID"
                }
            },
            "required": []
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "文件接口",
        "doc_url": "https://napcat.apifox.cn/226657066e0",
    },
    {
        "name": "get_mini_app_ark",
        "description": "获取小程序 Ark",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        },
        "risk": "read",
        "permission": "owner",
        "scopes": [
            "*"
        ],
        "category": "系统扩展",
        "doc_url": "https://napcat.apifox.cn/227738594e0",
    },
    {
        "name": "get_online_clients",
        "description": "获取当前登录账号的在线客户端列表",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        },
        "risk": "read",
        "permission": "owner",
        "scopes": [
            "*"
        ],
        "category": "Go-CQHTTP",
        "doc_url": "https://napcat.apifox.cn/226657379e0",
    },
    {
        "name": "get_online_file_msg",
        "description": "获取在线文件消息",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "用户 QQ"
                }
            },
            "required": [
                "user_id"
            ]
        },
        "risk": "read",
        "permission": "owner",
        "scopes": [
            "private"
        ],
        "category": "文件扩展",
        "doc_url": "https://napcat.apifox.cn/410334672e0",
    },
    {
        "name": "get_private_file_url",
        "description": "获取指定私聊文件的下载链接",
        "parameters": {
            "type": "object",
            "properties": {
                "file_id": {
                    "type": "string",
                    "description": "文件ID"
                }
            },
            "required": [
                "file_id"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "private"
        ],
        "category": "文件接口",
        "doc_url": "https://napcat.apifox.cn/266151849e0",
    },
    {
        "name": "get_profile_like",
        "description": "获取资料点赞",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "QQ号"
                },
                "start": {
                    "type": "number",
                    "description": "起始位置"
                },
                "count": {
                    "type": "number",
                    "description": "获取数量"
                }
            },
            "required": [
                "start",
                "count"
            ]
        },
        "risk": "read",
        "permission": "owner",
        "scopes": [
            "private"
        ],
        "category": "用户扩展",
        "doc_url": "https://napcat.apifox.cn/226659197e0",
    },
    {
        "name": "get_qun_album_list",
        "description": "获取群相册列表",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "attach_info": {
                    "type": "string",
                    "description": "附加信息（用于分页，从上一次返回结果中获取）"
                }
            },
            "required": [
                "group_id"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "group"
        ],
        "category": "群组扩展",
        "doc_url": "https://napcat.apifox.cn/395460287e0",
    },
    {
        "name": "get_recent_contact",
        "description": "获取最近会话",
        "parameters": {
            "type": "object",
            "properties": {
                "count": {
                    "type": "number",
                    "description": "获取的数量"
                }
            },
            "required": [
                "count"
            ]
        },
        "risk": "read",
        "permission": "owner",
        "scopes": [
            "private"
        ],
        "category": "用户接口",
        "doc_url": "https://napcat.apifox.cn/226659190e0",
    },
    {
        "name": "get_record",
        "description": "获取指定语音文件的信息，并支持格式转换",
        "parameters": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "文件路径、URL或Base64"
                },
                "file_id": {
                    "type": "string",
                    "description": "文件ID"
                },
                "out_format": {
                    "type": "string",
                    "description": "输出格式"
                }
            },
            "required": [
                "out_format"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "文件接口",
        "doc_url": "https://napcat.apifox.cn/226657058e0",
    },
    {
        "name": "get_rkey",
        "description": "获取扩展 RKey",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        },
        "risk": "read",
        "permission": "owner",
        "scopes": [
            "*"
        ],
        "category": "系统扩展",
        "doc_url": "https://napcat.apifox.cn/283136230e0",
    },
    {
        "name": "get_rkey_server",
        "description": "获取 RKey 服务器",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        },
        "risk": "read",
        "permission": "owner",
        "scopes": [
            "*"
        ],
        "category": "系统扩展",
        "doc_url": "https://napcat.apifox.cn/283136236e0",
    },
    {
        "name": "get_robot_uin_range",
        "description": "获取机器人 UIN 范围",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        },
        "risk": "read",
        "permission": "owner",
        "scopes": [
            "*"
        ],
        "category": "系统扩展",
        "doc_url": "https://napcat.apifox.cn/226658975e0",
    },
    {
        "name": "get_share_link",
        "description": "获取文件分享链接",
        "parameters": {
            "type": "object",
            "properties": {
                "fileset_id": {
                    "type": "string",
                    "description": "文件集 ID"
                }
            },
            "required": [
                "fileset_id"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "文件扩展",
        "doc_url": "https://napcat.apifox.cn/410334670e0",
    },
    {
        "name": "get_unidirectional_friend_list",
        "description": "获取单向好友列表",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "private"
        ],
        "category": "用户扩展",
        "doc_url": "https://napcat.apifox.cn/266151878e0",
    },
    {
        "name": "group_poke",
        "description": "在群聊或私聊中发送戳一戳动作",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "user_id": {
                    "type": "string",
                    "description": "用户QQ"
                },
                "target_id": {
                    "type": "string",
                    "description": "目标QQ"
                }
            },
            "required": [
                "user_id"
            ]
        },
        "risk": "send",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "核心接口",
        "doc_url": "https://napcat.apifox.cn/226659265e0",
    },
    {
        "name": "mark_group_msg_as_read",
        "description": "标记指定渠道的消息为已读",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "用户QQ"
                },
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "message_id": {
                    "type": "string",
                    "description": "消息ID"
                }
            },
            "required": []
        },
        "risk": "send",
        "permission": "member",
        "scopes": [
            "group"
        ],
        "category": "消息接口",
        "doc_url": "https://napcat.apifox.cn/226659167e0",
    },
    {
        "name": "mark_msg_as_read",
        "description": "标记指定渠道的消息为已读",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "用户QQ"
                },
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "message_id": {
                    "type": "string",
                    "description": "消息ID"
                }
            },
            "required": []
        },
        "risk": "send",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "消息接口",
        "doc_url": "https://napcat.apifox.cn/226657389e0",
    },
    {
        "name": "mark_private_msg_as_read",
        "description": "标记指定渠道的消息为已读",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "用户QQ"
                },
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "message_id": {
                    "type": "string",
                    "description": "消息ID"
                }
            },
            "required": []
        },
        "risk": "send",
        "permission": "member",
        "scopes": [
            "private"
        ],
        "category": "消息接口",
        "doc_url": "https://napcat.apifox.cn/226659165e0",
    },
    {
        "name": "move_group_file",
        "description": "移动群文件",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "file_id": {
                    "type": "string",
                    "description": "文件ID"
                },
                "current_parent_directory": {
                    "type": "string",
                    "description": "当前父目录"
                },
                "target_parent_directory": {
                    "type": "string",
                    "description": "目标父目录"
                }
            },
            "required": [
                "group_id",
                "file_id",
                "current_parent_directory",
                "target_parent_directory"
            ]
        },
        "risk": "admin",
        "permission": "group_owner",
        "scopes": [
            "group"
        ],
        "category": "文件扩展",
        "doc_url": "https://napcat.apifox.cn/283136359e0",
    },
    {
        "name": "nc_get_packet_status",
        "description": "获取底层Packet服务的运行状态",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        },
        "risk": "read",
        "permission": "owner",
        "scopes": [
            "*"
        ],
        "category": "系统接口",
        "doc_url": "https://napcat.apifox.cn/226659280e0",
    },
    {
        "name": "nc_get_rkey",
        "description": "获取 RKey",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        },
        "risk": "read",
        "permission": "owner",
        "scopes": [
            "*"
        ],
        "category": "系统扩展",
        "doc_url": "https://napcat.apifox.cn/226659297e0",
    },
    {
        "name": "nc_get_user_status",
        "description": "获取用户在线状态",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "QQ号"
                }
            },
            "required": [
                "user_id"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "系统扩展",
        "doc_url": "https://napcat.apifox.cn/226659292e0",
    },
    {
        "name": "ocr_image",
        "description": "识别图片中的文字内容(仅Windows端支持)",
        "parameters": {
            "type": "object",
            "properties": {
                "image": {
                    "type": "string",
                    "description": "图片路径、URL或Base64"
                }
            },
            "required": [
                "image"
            ]
        },
        "risk": "send",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "扩展接口",
        "doc_url": "https://napcat.apifox.cn/226658231e0",
    },
    {
        "name": "receive_online_file",
        "description": "接收在线文件",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "用户 QQ"
                },
                "msg_id": {
                    "type": "string",
                    "description": "消息 ID"
                },
                "element_id": {
                    "type": "string",
                    "description": "元素 ID"
                }
            },
            "required": [
                "user_id",
                "msg_id",
                "element_id"
            ]
        },
        "risk": "send",
        "permission": "member",
        "scopes": [
            "private"
        ],
        "category": "文件扩展",
        "doc_url": "https://napcat.apifox.cn/410334675e0",
    },
    {
        "name": "refuse_online_file",
        "description": "拒绝在线文件",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "用户 QQ"
                },
                "msg_id": {
                    "type": "string",
                    "description": "消息 ID"
                },
                "element_id": {
                    "type": "string",
                    "description": "元素 ID"
                }
            },
            "required": [
                "user_id",
                "msg_id",
                "element_id"
            ]
        },
        "risk": "send",
        "permission": "member",
        "scopes": [
            "private"
        ],
        "category": "文件扩展",
        "doc_url": "https://napcat.apifox.cn/410334676e0",
    },
    {
        "name": "rename_group_file",
        "description": "重命名群文件",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "file_id": {
                    "type": "string",
                    "description": "文件ID"
                },
                "current_parent_directory": {
                    "type": "string",
                    "description": "当前父目录"
                },
                "new_name": {
                    "type": "string",
                    "description": "新文件名"
                }
            },
            "required": [
                "group_id",
                "file_id",
                "current_parent_directory",
                "new_name"
            ]
        },
        "risk": "admin",
        "permission": "group_owner",
        "scopes": [
            "group"
        ],
        "category": "文件扩展",
        "doc_url": "https://napcat.apifox.cn/283136375e0",
    },
    {
        "name": "send_ark_share",
        "description": "获取用户推荐的 Ark 内容",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "QQ号"
                },
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "phone_number": {
                    "type": "string",
                    "description": "手机号"
                }
            },
            "required": [
                "phone_number"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "private"
        ],
        "category": "消息扩展",
        "doc_url": "https://napcat.apifox.cn/410334665e0",
    },
    {
        "name": "send_flash_msg",
        "description": "发送闪传消息",
        "parameters": {
            "type": "object",
            "properties": {
                "fileset_id": {
                    "type": "string",
                    "description": "文件集 ID"
                },
                "user_id": {
                    "type": "string",
                    "description": "用户 QQ"
                },
                "group_id": {
                    "type": "string",
                    "description": "群号"
                }
            },
            "required": [
                "fileset_id"
            ]
        },
        "risk": "send",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "文件扩展",
        "doc_url": "https://napcat.apifox.cn/410334669e0",
    },
    {
        "name": "send_forward_msg",
        "description": "发送合并转发消息",
        "parameters": {
            "type": "object",
            "properties": {
                "message_type": {
                    "type": "string",
                    "description": "消息类型 (private/group)",
                    "enum": [
                        "private",
                        "group"
                    ]
                },
                "user_id": {
                    "type": "string",
                    "description": "用户QQ"
                },
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "auto_escape": {
                    "type": "boolean",
                    "description": "是否作为纯文本发送"
                },
                "source": {
                    "type": "string",
                    "description": "合并转发来源"
                },
                "news": {
                    "type": "array",
                    "description": "合并转发新闻",
                    "items": {
                        "type": "object"
                    }
                },
                "summary": {
                    "type": "string",
                    "description": "合并转发摘要"
                },
                "prompt": {
                    "type": "string",
                    "description": "合并转发提示"
                },
                "timeout": {
                    "type": "number",
                    "description": "自定义发送超时(毫秒)，覆盖自动计算值"
                },
                "messages": {
                    "type": "array",
                    "items": {
                        "type": "object"
                    },
                    "description": "合并转发消息节点数组"
                }
            },
            "required": [
                "messages"
            ]
        },
        "risk": "send",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "Go-CQHTTP",
        "doc_url": "https://napcat.apifox.cn/226659136e0",
    },
    {
        "name": "send_group_ai_record",
        "description": "发送 AI 生成的语音到指定群聊",
        "parameters": {
            "type": "object",
            "properties": {
                "character": {
                    "type": "string",
                    "description": "角色ID"
                },
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "text": {
                    "type": "string",
                    "description": "语音文本内容"
                }
            },
            "required": [
                "character",
                "group_id",
                "text"
            ]
        },
        "risk": "send",
        "permission": "member",
        "scopes": [
            "group"
        ],
        "category": "AI 扩展",
        "doc_url": "https://napcat.apifox.cn/229486774e0",
    },
    {
        "name": "send_group_ark_share",
        "description": "获取群分享的 Ark 内容",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                }
            },
            "required": [
                "group_id"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "group"
        ],
        "category": "消息扩展",
        "doc_url": "https://napcat.apifox.cn/410334664e0",
    },
    {
        "name": "send_group_forward_msg",
        "description": "发送群合并转发消息",
        "parameters": {
            "type": "object",
            "properties": {
                "message_type": {
                    "type": "string",
                    "description": "消息类型 (private/group)",
                    "enum": [
                        "private",
                        "group"
                    ]
                },
                "user_id": {
                    "type": "string",
                    "description": "用户QQ"
                },
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "auto_escape": {
                    "type": "boolean",
                    "description": "是否作为纯文本发送"
                },
                "source": {
                    "type": "string",
                    "description": "合并转发来源"
                },
                "news": {
                    "type": "array",
                    "description": "合并转发新闻",
                    "items": {
                        "type": "object"
                    }
                },
                "summary": {
                    "type": "string",
                    "description": "合并转发摘要"
                },
                "prompt": {
                    "type": "string",
                    "description": "合并转发提示"
                },
                "timeout": {
                    "type": "number",
                    "description": "自定义发送超时(毫秒)，覆盖自动计算值"
                },
                "messages": {
                    "type": "array",
                    "items": {
                        "type": "object"
                    },
                    "description": "合并转发消息节点数组"
                }
            },
            "required": [
                "messages"
            ]
        },
        "risk": "send",
        "permission": "member",
        "scopes": [
            "group"
        ],
        "category": "Go-CQHTTP",
        "doc_url": "https://napcat.apifox.cn/226657396e0",
    },
    {
        "name": "send_msg",
        "description": "发送私聊或群聊消息",
        "parameters": {
            "type": "object",
            "properties": {
                "message_type": {
                    "type": "string",
                    "description": "消息类型 (private/group)",
                    "enum": [
                        "private",
                        "group"
                    ]
                },
                "user_id": {
                    "type": "string",
                    "description": "用户QQ"
                },
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "message": {
                    "type": "string",
                    "description": ""
                },
                "auto_escape": {
                    "type": "boolean",
                    "description": "是否作为纯文本发送"
                },
                "source": {
                    "type": "string",
                    "description": "合并转发来源"
                },
                "news": {
                    "type": "array",
                    "description": "合并转发新闻",
                    "items": {
                        "type": "object"
                    }
                },
                "summary": {
                    "type": "string",
                    "description": "合并转发摘要"
                },
                "prompt": {
                    "type": "string",
                    "description": "合并转发提示"
                },
                "timeout": {
                    "type": "number",
                    "description": "自定义发送超时(毫秒)，覆盖自动计算值"
                }
            },
            "required": [
                "message"
            ]
        },
        "risk": "send",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "消息接口",
        "doc_url": "https://napcat.apifox.cn/226656652e0",
    },
    {
        "name": "send_online_file",
        "description": "发送在线文件",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "用户 QQ"
                },
                "file_path": {
                    "type": "string",
                    "description": "本地文件路径"
                },
                "file_name": {
                    "type": "string",
                    "description": "文件名 (可选)"
                }
            },
            "required": [
                "user_id",
                "file_path"
            ]
        },
        "risk": "send",
        "permission": "member",
        "scopes": [
            "private"
        ],
        "category": "文件扩展",
        "doc_url": "https://napcat.apifox.cn/410334673e0",
    },
    {
        "name": "send_online_folder",
        "description": "发送在线文件夹",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "用户 QQ"
                },
                "folder_path": {
                    "type": "string",
                    "description": "本地文件夹路径"
                },
                "folder_name": {
                    "type": "string",
                    "description": "文件夹名称 (可选)"
                }
            },
            "required": [
                "user_id",
                "folder_path"
            ]
        },
        "risk": "send",
        "permission": "member",
        "scopes": [
            "private"
        ],
        "category": "文件扩展",
        "doc_url": "https://napcat.apifox.cn/410334674e0",
    },
    {
        "name": "send_packet",
        "description": "发送原始数据包",
        "parameters": {
            "type": "object",
            "properties": {
                "cmd": {
                    "type": "string",
                    "description": "命令字"
                },
                "data": {
                    "type": "string",
                    "description": "十六进制数据"
                },
                "rsp": {
                    "type": "string",
                    "description": "是否等待响应"
                }
            },
            "required": [
                "cmd",
                "data",
                "rsp"
            ]
        },
        "risk": "send",
        "permission": "owner",
        "scopes": [
            "*"
        ],
        "category": "系统扩展",
        "doc_url": "https://napcat.apifox.cn/250286903e0",
    },
    {
        "name": "send_private_forward_msg",
        "description": "发送私聊合并转发消息",
        "parameters": {
            "type": "object",
            "properties": {
                "message_type": {
                    "type": "string",
                    "description": "消息类型 (private/group)",
                    "enum": [
                        "private",
                        "group"
                    ]
                },
                "user_id": {
                    "type": "string",
                    "description": "用户QQ"
                },
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "auto_escape": {
                    "type": "boolean",
                    "description": "是否作为纯文本发送"
                },
                "source": {
                    "type": "string",
                    "description": "合并转发来源"
                },
                "news": {
                    "type": "array",
                    "description": "合并转发新闻",
                    "items": {
                        "type": "object"
                    }
                },
                "summary": {
                    "type": "string",
                    "description": "合并转发摘要"
                },
                "prompt": {
                    "type": "string",
                    "description": "合并转发提示"
                },
                "timeout": {
                    "type": "number",
                    "description": "自定义发送超时(毫秒)，覆盖自动计算值"
                },
                "messages": {
                    "type": "array",
                    "items": {
                        "type": "object"
                    },
                    "description": "合并转发消息节点数组"
                }
            },
            "required": [
                "messages"
            ]
        },
        "risk": "send",
        "permission": "member",
        "scopes": [
            "private"
        ],
        "category": "Go-CQHTTP",
        "doc_url": "https://napcat.apifox.cn/226657399e0",
    },
    {
        "name": "send_qzone_msg",
        "description": "在QQ空间(Qzone)发表说说, 支持纯文字或带(多)图, 可设置查看权限",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "说说正文"
                },
                "images": {
                    "type": "array",
                    "description": "图片数组, 支持 file:// http(s):// base64:// , 自动上传",
                    "items": {
                        "type": "string"
                    }
                },
                "ugc_right": {
                    "type": "number",
                    "description": "查看权限: 1所有人可见 4好友可见 16部分好友可见 64仅自己可见 128部分好友不可见"
                },
                "target_uins": {
                    "type": "array",
                    "description": "ugc_right为16/128时, 权限作用的QQ号数组",
                    "items": {
                        "type": "number"
                    }
                }
            },
            "required": [
                "content"
            ]
        },
        "risk": "send",
        "permission": "owner",
        "scopes": [
            "*"
        ],
        "category": "扩展接口",
        "doc_url": "https://napcat.apifox.cn/496813058e0",
    },
    {
        "name": "set_custom_face_desc",
        "description": "修改自定义表情描述",
        "parameters": {
            "type": "object",
            "properties": {
                "emoji_id": {
                    "type": "number",
                    "description": "表情ID"
                },
                "res_id": {
                    "type": "string",
                    "description": "资源ID"
                },
                "md5": {
                    "type": "string",
                    "description": "表情MD5"
                },
                "desc": {
                    "type": "string",
                    "description": "新的表情描述"
                }
            },
            "required": [
                "emoji_id",
                "res_id",
                "md5",
                "desc"
            ]
        },
        "risk": "admin",
        "permission": "owner",
        "scopes": [
            "*"
        ],
        "category": "系统扩展",
        "doc_url": "https://napcat.apifox.cn/467693195e0",
    },
    {
        "name": "set_diy_online_status",
        "description": "设置自定义在线状态",
        "parameters": {
            "type": "object",
            "properties": {
                "face_id": {
                    "type": "number",
                    "description": "图标ID"
                },
                "face_type": {
                    "type": "number",
                    "description": "图标类型"
                },
                "wording": {
                    "type": "string",
                    "description": "状态文字内容"
                }
            },
            "required": [
                "face_id",
                "face_type",
                "wording"
            ]
        },
        "risk": "admin",
        "permission": "owner",
        "scopes": [
            "*"
        ],
        "category": "用户扩展",
        "doc_url": "https://napcat.apifox.cn/266151905e0",
    },
    {
        "name": "set_doubt_friends_add_request",
        "description": "同意或拒绝系统的可疑好友申请",
        "parameters": {
            "type": "object",
            "properties": {
                "flag": {
                    "type": "string",
                    "description": "请求 flag"
                },
                "approve": {
                    "type": "boolean",
                    "description": "是否同意 (强制为 True)"
                }
            },
            "required": [
                "flag",
                "approve"
            ]
        },
        "risk": "admin",
        "permission": "owner",
        "scopes": [
            "private"
        ],
        "category": "系统接口",
        "doc_url": "https://napcat.apifox.cn/289565525e0",
    },
    {
        "name": "set_friend_remark",
        "description": "设置好友备注",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "对方 QQ 号"
                },
                "remark": {
                    "type": "string",
                    "description": "备注内容"
                }
            },
            "required": [
                "user_id",
                "remark"
            ]
        },
        "risk": "admin",
        "permission": "owner",
        "scopes": [
            "private"
        ],
        "category": "用户接口",
        "doc_url": "https://napcat.apifox.cn/298305173e0",
    },
    {
        "name": "set_group_add_option",
        "description": "设置群加群选项",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "add_type": {
                    "type": "number",
                    "description": "加群方式"
                },
                "group_question": {
                    "type": "string",
                    "description": "加群问题"
                },
                "group_answer": {
                    "type": "string",
                    "description": "加群答案"
                }
            },
            "required": [
                "group_id",
                "add_type"
            ]
        },
        "risk": "admin",
        "permission": "group_owner",
        "scopes": [
            "group"
        ],
        "category": "群组扩展",
        "doc_url": "https://napcat.apifox.cn/301542178e0",
    },
    {
        "name": "set_group_album_media_like",
        "description": "点赞群相册媒体",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "album_id": {
                    "type": "string",
                    "description": "相册ID"
                },
                "batch_id": {
                    "type": "string",
                    "description": "batch_id"
                },
                "lloc": {
                    "type": "string",
                    "description": "lloc，若对整个上传操作则不填"
                }
            },
            "required": [
                "group_id",
                "album_id",
                "batch_id"
            ]
        },
        "risk": "send",
        "permission": "group_owner",
        "scopes": [
            "group"
        ],
        "category": "群组扩展",
        "doc_url": "https://napcat.apifox.cn/395457331e0",
    },
    {
        "name": "set_group_kick_members",
        "description": "从指定群聊中批量踢出多个成员",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "user_id": {
                    "type": "array",
                    "description": "QQ号列表",
                    "items": {
                        "type": "string"
                    }
                },
                "reject_add_request": {
                    "type": "boolean",
                    "description": "是否拒绝加群请求"
                }
            },
            "required": [
                "group_id",
                "user_id"
            ]
        },
        "risk": "admin",
        "permission": "group_owner",
        "scopes": [
            "group"
        ],
        "category": "扩展接口",
        "doc_url": "https://napcat.apifox.cn/301542209e0",
    },
    {
        "name": "set_group_member_invite_policy",
        "description": "设置是否允许群成员邀请好友进群，以及邀请是否需要管理员审核",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "policy": {
                    "type": "string",
                    "description": "成员邀请策略：禁止、需要管理员审核、无需审核、群成员少于100人时无需审核",
                    "enum": [
                        "disabled",
                        "require_approval",
                        "no_approval",
                        "no_approval_under_100"
                    ]
                }
            },
            "required": [
                "group_id",
                "policy"
            ]
        },
        "risk": "admin",
        "permission": "group_owner",
        "scopes": [
            "group"
        ],
        "category": "群组接口",
        "doc_url": "https://napcat.apifox.cn/496813055e0",
    },
    {
        "name": "set_group_member_permissions",
        "description": "设置群成员上传相册、发起临时会话和发起新群聊的权限；未传入的项目保持不变",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "allow_member_upload_album": {
                    "type": "boolean",
                    "description": "允许成员上传群相册"
                },
                "allow_member_temporary_session": {
                    "type": "boolean",
                    "description": "允许成员发起临时会话"
                },
                "allow_member_create_group": {
                    "type": "boolean",
                    "description": "允许成员发起新的群聊"
                }
            },
            "required": [
                "group_id"
            ]
        },
        "risk": "admin",
        "permission": "group_owner",
        "scopes": [
            "group"
        ],
        "category": "群组接口",
        "doc_url": "https://napcat.apifox.cn/496813056e0",
    },
    {
        "name": "set_group_new_member_history_visibility",
        "description": "设置新入群成员默认是否可以查看最近聊天记录",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "visible": {
                    "type": "boolean",
                    "description": "新成员默认可见最近聊天记录"
                }
            },
            "required": [
                "group_id",
                "visible"
            ]
        },
        "risk": "admin",
        "permission": "group_owner",
        "scopes": [
            "group"
        ],
        "category": "群组接口",
        "doc_url": "https://napcat.apifox.cn/496813057e0",
    },
    {
        "name": "set_group_portrait",
        "description": "修改指定群聊的头像",
        "parameters": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "头像文件路径或 URL"
                },
                "group_id": {
                    "type": "string",
                    "description": "群号"
                }
            },
            "required": [
                "file",
                "group_id"
            ]
        },
        "risk": "admin",
        "permission": "group_owner",
        "scopes": [
            "group"
        ],
        "category": "Go-CQHTTP",
        "doc_url": "https://napcat.apifox.cn/226658669e0",
    },
    {
        "name": "set_group_remark",
        "description": "设置群备注",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "remark": {
                    "type": "string",
                    "description": "备注"
                }
            },
            "required": [
                "group_id",
                "remark"
            ]
        },
        "risk": "admin",
        "permission": "group_owner",
        "scopes": [
            "group"
        ],
        "category": "群组扩展",
        "doc_url": "https://napcat.apifox.cn/283136268e0",
    },
    {
        "name": "set_group_robot_add_option",
        "description": "设置群机器人加群选项",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "robot_member_switch": {
                    "type": "number",
                    "description": "机器人成员开关"
                },
                "robot_member_examine": {
                    "type": "number",
                    "description": "机器人成员审核"
                }
            },
            "required": [
                "group_id"
            ]
        },
        "risk": "admin",
        "permission": "group_owner",
        "scopes": [
            "group"
        ],
        "category": "群组扩展",
        "doc_url": "https://napcat.apifox.cn/301542198e0",
    },
    {
        "name": "set_group_search",
        "description": "设置群搜索选项",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "no_code_finger_open": {
                    "type": "number",
                    "description": "未知"
                },
                "no_finger_open": {
                    "type": "number",
                    "description": "未知"
                }
            },
            "required": [
                "group_id"
            ]
        },
        "risk": "admin",
        "permission": "group_owner",
        "scopes": [
            "group"
        ],
        "category": "群组扩展",
        "doc_url": "https://napcat.apifox.cn/301542170e0",
    },
    {
        "name": "set_group_sign",
        "description": "群打卡",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                }
            },
            "required": [
                "group_id"
            ]
        },
        "risk": "send",
        "permission": "group_admin",
        "scopes": [
            "group"
        ],
        "category": "群组扩展",
        "doc_url": "https://napcat.apifox.cn/226659329e0",
    },
    {
        "name": "set_group_special_title",
        "description": "设置群聊中指定成员的专属头衔",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "user_id": {
                    "type": "string",
                    "description": "QQ号"
                },
                "special_title": {
                    "type": "string",
                    "description": "专属头衔"
                }
            },
            "required": [
                "group_id",
                "user_id",
                "special_title"
            ]
        },
        "risk": "admin",
        "permission": "group_admin",
        "scopes": [
            "group"
        ],
        "category": "扩展接口",
        "doc_url": "https://napcat.apifox.cn/226656931e0",
    },
    {
        "name": "set_group_todo",
        "description": "将指定消息设置为群待办",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "message_id": {
                    "type": "string",
                    "description": "消息ID"
                },
                "message_seq": {
                    "type": "string",
                    "description": "消息Seq (可选)"
                }
            },
            "required": [
                "group_id"
            ]
        },
        "risk": "send",
        "permission": "group_admin",
        "scopes": [
            "group"
        ],
        "category": "核心接口",
        "doc_url": "https://napcat.apifox.cn/395460568e0",
    },
    {
        "name": "set_input_status",
        "description": "设置输入状态",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "QQ号"
                },
                "event_type": {
                    "type": "number",
                    "description": "事件类型"
                }
            },
            "required": [
                "user_id",
                "event_type"
            ]
        },
        "risk": "send",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "系统扩展",
        "doc_url": "https://napcat.apifox.cn/226659225e0",
    },
    {
        "name": "set_online_status",
        "description": "",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "number",
                    "description": "在线状态"
                },
                "ext_status": {
                    "type": "number",
                    "description": "扩展状态"
                },
                "battery_status": {
                    "type": "number",
                    "description": "电量状态"
                }
            },
            "required": [
                "status",
                "ext_status",
                "battery_status"
            ]
        },
        "risk": "admin",
        "permission": "owner",
        "scopes": [
            "*"
        ],
        "category": "系统扩展",
        "doc_url": "https://napcat.apifox.cn/226658977e0",
    },
    {
        "name": "set_qq_avatar",
        "description": "修改当前账号的QQ头像",
        "parameters": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "图片路径、URL或Base64"
                }
            },
            "required": [
                "file"
            ]
        },
        "risk": "admin",
        "permission": "owner",
        "scopes": [
            "*"
        ],
        "category": "扩展接口",
        "doc_url": "https://napcat.apifox.cn/226658980e0",
    },
    {
        "name": "set_qq_profile",
        "description": "修改当前账号的昵称、个性签名等资料",
        "parameters": {
            "type": "object",
            "properties": {
                "nickname": {
                    "type": "string",
                    "description": "昵称"
                },
                "personal_note": {
                    "type": "string",
                    "description": "个性签名"
                },
                "sex": {
                    "type": "number",
                    "description": "性别 (0: 未知, 1: 男, 2: 女)"
                }
            },
            "required": [
                "nickname"
            ]
        },
        "risk": "admin",
        "permission": "owner",
        "scopes": [
            "*"
        ],
        "category": "Go-CQHTTP",
        "doc_url": "https://napcat.apifox.cn/226657374e0",
    },
    {
        "name": "set_restart",
        "description": "重启服务",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        },
        "risk": "admin",
        "permission": "owner",
        "scopes": [
            "*"
        ],
        "category": "系统接口",
        "doc_url": "https://napcat.apifox.cn/410334662e0",
    },
    {
        "name": "set_self_longnick",
        "description": "修改当前登录帐号的个性签名",
        "parameters": {
            "type": "object",
            "properties": {
                "longNick": {
                    "type": "string",
                    "description": "签名内容"
                }
            },
            "required": [
                "longNick"
            ]
        },
        "risk": "admin",
        "permission": "owner",
        "scopes": [
            "private"
        ],
        "category": "扩展接口",
        "doc_url": "https://napcat.apifox.cn/226659186e0",
    },
    {
        "name": "test_download_stream",
        "description": "测试下载流",
        "parameters": {
            "type": "object",
            "properties": {
                "error": {
                    "type": "boolean",
                    "description": "是否触发测试错误"
                }
            },
            "required": []
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "流式传输扩展",
        "doc_url": "https://napcat.apifox.cn/395355338e0",
    },
    {
        "name": "trans_group_file",
        "description": "传输群文件",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "file_id": {
                    "type": "string",
                    "description": "文件ID"
                }
            },
            "required": [
                "group_id",
                "file_id"
            ]
        },
        "risk": "admin",
        "permission": "group_owner",
        "scopes": [
            "group"
        ],
        "category": "文件扩展",
        "doc_url": "https://napcat.apifox.cn/283136366e0",
    },
    {
        "name": "translate_en2zh",
        "description": "将英文单词列表翻译为中文",
        "parameters": {
            "type": "object",
            "properties": {
                "words": {
                    "type": "array",
                    "description": "待翻译单词列表",
                    "items": {
                        "type": "string"
                    }
                }
            },
            "required": [
                "words"
            ]
        },
        "risk": "read",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "扩展接口",
        "doc_url": "https://napcat.apifox.cn/226659102e0",
    },
    {
        "name": "upload_file_stream",
        "description": "以流式方式上传文件数据到机器人",
        "parameters": {
            "type": "object",
            "properties": {
                "stream_id": {
                    "type": "string",
                    "description": "流 ID"
                },
                "chunk_data": {
                    "type": "string",
                    "description": "分块数据 (Base64)"
                },
                "chunk_index": {
                    "type": "number",
                    "description": "分块索引"
                },
                "total_chunks": {
                    "type": "number",
                    "description": "总分块数"
                },
                "file_size": {
                    "type": "number",
                    "description": "文件总大小"
                },
                "expected_sha256": {
                    "type": "string",
                    "description": "期望的 SHA256"
                },
                "is_complete": {
                    "type": "boolean",
                    "description": "是否完成"
                },
                "filename": {
                    "type": "string",
                    "description": "文件名"
                },
                "reset": {
                    "type": "boolean",
                    "description": "是否重置"
                },
                "verify_only": {
                    "type": "boolean",
                    "description": "是否仅验证"
                },
                "file_retention": {
                    "type": "number",
                    "description": "文件保留时间 (毫秒)"
                }
            },
            "required": [
                "stream_id",
                "file_retention"
            ]
        },
        "risk": "send",
        "permission": "member",
        "scopes": [
            "*"
        ],
        "category": "流式接口",
        "doc_url": "https://napcat.apifox.cn/395363988e0",
    },
    {
        "name": "upload_group_file",
        "description": "上传资源路径或URL指定的文件到指定群聊的文件系统中",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "file": {
                    "type": "string",
                    "description": "资源路径或URL"
                },
                "name": {
                    "type": "string",
                    "description": "文件名"
                },
                "folder": {
                    "type": "string",
                    "description": "父目录 ID"
                },
                "folder_id": {
                    "type": "string",
                    "description": "父目录 ID (兼容性字段)"
                },
                "upload_file": {
                    "type": "boolean",
                    "description": "是否执行上传"
                }
            },
            "required": [
                "group_id",
                "file",
                "name",
                "upload_file"
            ]
        },
        "risk": "send",
        "permission": "group_owner",
        "scopes": [
            "group"
        ],
        "category": "Go-CQHTTP",
        "doc_url": "https://napcat.apifox.cn/226658753e0",
    },
    {
        "name": "upload_image_to_qun_album",
        "description": "上传图片到群相册",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群号"
                },
                "album_id": {
                    "type": "string",
                    "description": "相册ID"
                },
                "album_name": {
                    "type": "string",
                    "description": "相册名称"
                },
                "file": {
                    "type": "string",
                    "description": "图片路径、URL或Base64"
                }
            },
            "required": [
                "group_id",
                "album_id",
                "album_name",
                "file"
            ]
        },
        "risk": "send",
        "permission": "group_owner",
        "scopes": [
            "group"
        ],
        "category": "群组扩展",
        "doc_url": "https://napcat.apifox.cn/395459739e0",
    },
    {
        "name": "upload_private_file",
        "description": "上传本地文件到指定私聊会话中",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "用户 QQ"
                },
                "file": {
                    "type": "string",
                    "description": "资源路径或URL"
                },
                "name": {
                    "type": "string",
                    "description": "文件名"
                },
                "upload_file": {
                    "type": "boolean",
                    "description": "是否执行上传"
                }
            },
            "required": [
                "user_id",
                "file",
                "name",
                "upload_file"
            ]
        },
        "risk": "send",
        "permission": "member",
        "scopes": [
            "private"
        ],
        "category": "Go-CQHTTP",
        "doc_url": "https://napcat.apifox.cn/226658883e0",
    },
]
# ==================== 敏感度标注（写死，不随前端编辑改变） ====================
_CRITICAL_TOOLS = {
    "set_group_leave",
    "set_group_whole_ban",
    "set_group_kick",
    "set_group_admin",
    "set_group_name",
    "clean_cache",
}
_HIGH_TOOLS = {
    "delete_msg",
    "set_essence_msg",
    "delete_essence_msg",
    "set_group_card",
    "set_group_ban",
    "set_group_add_request",
    "set_friend_add_request",
}

for _tool in NAP_CAT_TOOLS:
    _name = str(_tool.get("name", ""))
    if _name in _CRITICAL_TOOLS:
        _tool["sensitivity"] = "critical"
    elif _name in _HIGH_TOOLS:
        _tool["sensitivity"] = "high"
    else:
        _tool["sensitivity"] = "normal"
