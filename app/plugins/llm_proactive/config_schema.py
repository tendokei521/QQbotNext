"""主动消息模块的配置 Schema（原 agent「主动消息」组，共 17 键）。"""

SCHEMA = {
    "proactive_friend_enable": {
        "type": "boolean",
        "label": "私聊主动发言",
        "description": "对下方私聊会话按随机间隔主动发言",
        "default": False,
    },
    "proactive_group_enable": {
        "type": "boolean",
        "label": "群聊主动发言",
        "description": "群聊沉默一段时间后主动开口",
        "default": False,
    },
    "proactive_friend_sessions": {
        "type": "string_list",
        "label": "私聊会话（QQ号）",
        "description": "对哪些 QQ 主动发言，每行一个",
        "default": [],
    },
    "proactive_group_sessions": {
        "type": "string_list",
        "label": "群聊会话（群号）",
        "description": "对哪些群主动发言，每行一个",
        "default": [],
    },
    "proactive_min_interval_minutes": {
        "type": "number",
        "label": "最短间隔(分钟)",
        "description": "私聊两次主动发言之间的最短等待",
        "default": 30,
    },
    "proactive_max_interval_minutes": {
        "type": "number",
        "label": "最长间隔(分钟)",
        "description": "私聊两次主动发言之间的最长等待",
        "default": 900,
    },
    "proactive_max_unanswered": {
        "type": "number",
        "label": "最多追问次数",
        "description": "连续无人接话达到此数即停止主动发言。0 = 不限。",
        "default": 3,
    },
    "proactive_quiet_hours_start": {
        "type": "number",
        "label": "免打扰开始(小时)",
        "description": "该小时起不主动发言（0-23）",
        "default": 1,
    },
    "proactive_quiet_hours_end": {
        "type": "number",
        "label": "免打扰结束(小时)",
        "description": "该小时起恢复（支持跨天，如 1→7）",
        "default": 7,
    },
    "proactive_group_idle_minutes": {
        "type": "number",
        "label": "群聊沉默阈值(分钟)",
        "description": "群里安静多久后主动开口",
        "default": 10,
    },
    "proactive_prompt": {
        "type": "textarea",
        "label": "主动发言提示词",
        "description": "留空用内置模板；支持 `{{current_time}}`、`{{unanswered_count}}` 占位。",
        "default": "",
    },
    "proactive_prompt_enable": {
        "type": "boolean",
        "label": "启用主动发言提示词",
        "description": "关掉则不注入主动性提示块",
        "default": True,
    },
    "proactive_history_intent_nudge": {
        "type": "boolean",
        "label": "历史意图提示",
        "description": "用户说「刚才/之前」时提示模型先查记录",
        "default": True,
    },
    "proactive_env_prompt_enable": {
        "type": "boolean",
        "label": "环境能力提示",
        "description": "按本轮可用能力给出环境查询提示",
        "default": True,
    },
    "proactive_unresolved_prompt_enable": {
        "type": "boolean",
        "label": "未展开内容提示",
        "description": "上下文里有未展开内容时，提示模型按需展开",
        "default": True,
    },
    "proactive_env_intent_nudge": {
        "type": "boolean",
        "label": "环境意图提示",
        "description": "问到群名/成员时提示可调用环境工具",
        "default": True,
    },
    "stream_proactive_enabled": {
        "type": "boolean",
        "label": "主动消息走流式",
        "description": "复用「输出策略」模块的流式配置",
        "default": False,
    },
}
