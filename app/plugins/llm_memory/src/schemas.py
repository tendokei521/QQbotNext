"""长期记忆模块的配置 Schema（v3，23 键）。

键的分区归属见 `app/agent/config_partition.py::MODULE_KEYS["llm_memory"]`；
本文件是 WebUI 表单与运行时 ``default_config`` 的唯一来源，两者必须一致
（`tests/plugins/memory/test_memory_module.py` 会校验）。
"""

from __future__ import annotations

MEMORY_SCHEMA: dict[str, dict] = {
    # ── 开关 ─────────────────────────────────────────────
    "memory_enable": {
        "type": "boolean",
        "label": "启用长期记忆",
        "description": "跨会话记住用户说过的偏好/事实，并在后续对话里召回",
        "default": False,
    },
    "memory_private_enable": {
        "type": "boolean",
        "label": "私聊记忆",
        "description": "私聊里是否记录与召回",
        "default": True,
    },
    "memory_group_enable": {
        "type": "boolean",
        "label": "群聊记忆",
        "description": "群聊里是否记录与召回（按群内成员隔离）",
        "default": True,
    },
    "memory_profile_enable": {
        "type": "boolean",
        "label": "用户画像常驻",
        "description": "把姓名/身份/住址/长期偏好这类画像常驻在提示词里，不依赖检索命中",
        "default": True,
    },
    # ── 召回预算 ─────────────────────────────────────────
    "memory_recall_mode": {
        "type": "select",
        "label": "召回方式",
        "description": "hybrid=关键词+向量混合（未配置 embedding 模型时自动退化为关键词）；keyword=只用关键词",
        "default": "hybrid",
        "options": {"hybrid": "混合（关键词+向量）", "keyword": "仅关键词"},
    },
    "memory_embedding_model_id": {
        "type": "resource",
        "resource": "provider_model",
        "filters": {"provider_type": "embedding"},
        "label": "Embedding 模型",
        "description": "用于混合召回与相似去重；留空则自动选第一个 Embedding 模型，没有则退化为关键词召回",
        "default": "",
    },
    "memory_recall_max": {
        "type": "number",
        "label": "召回条数上限",
        "description": "一次注入最多几条事实记忆",
        "default": 8,
        "min": 1,
        "max": 30,
        "step": 1,
    },
    "memory_recall_max_chars": {
        "type": "number",
        "label": "召回字符预算",
        "description": "事实记忆块的总字符上限",
        "default": 1200,
        "min": 100,
        "max": 4000,
        "step": 50,
    },
    "memory_profile_max_chars": {
        "type": "number",
        "label": "画像块字符预算",
        "description": "常驻画像块的最大字符数（超出按重要度截断）",
        "default": 800,
        "min": 100,
        "max": 3000,
        "step": 50,
    },
    # ── 写入 ─────────────────────────────────────────────
    "memory_save_deterministic": {
        "type": "boolean",
        "label": "确定性兜底写入",
        "description": "用户明确说「记住…」时不等模型，直接落库",
        "default": True,
    },
    "memory_extract_enable": {
        "type": "boolean",
        "label": "自动蒸馏",
        "description": "对话结束后用模型从原始语料里抽取值得长期记住的事实（后台异步，不增加回合延迟）",
        "default": True,
    },
    "memory_extract_interval_min": {
        "type": "number",
        "label": "蒸馏间隔(分钟)",
        "description": "同一会话两次自动蒸馏之间的最短间隔",
        "default": 10,
        "min": 1,
        "max": 1440,
        "step": 1,
    },
    "memory_consolidate_batch": {
        "type": "number",
        "label": "单次蒸馏条数",
        "description": "一次最多拿多少条未巩固的用户消息给模型抽取",
        "default": 20,
        "min": 5,
        "max": 100,
        "step": 5,
    },
    "memory_consolidate_min_chars": {
        "type": "number",
        "label": "蒸馏最小字数",
        "description": "累计原始语料少于该字数时不调用模型（省 token）",
        "default": 30,
        "min": 10,
        "max": 500,
        "step": 10,
    },
    "memory_max_per_owner": {
        "type": "number",
        "label": "每人记忆上限",
        "description": "同一 owner 保留的 active 记忆条数上限，超出按重要度×时间权重淘汰",
        "default": 200,
        "min": 10,
        "max": 5000,
        "step": 10,
    },
    "memory_user_cross_group": {
        "type": "boolean",
        "label": "跨群共享用户记忆",
        "description": "同一个人在不同群里共用一份画像（默认关闭，保护隐私）",
        "default": False,
    },
    # ── 可信度与生命周期 ─────────────────────────────────
    "memory_audit_enable": {
        "type": "boolean",
        "label": "记忆审计",
        "description": "记录写入/读取/注入/下架/清除事件（/memory audit 查看）",
        "default": True,
    },
    "memory_audit_inject": {
        "type": "boolean",
        "label": "审计注入",
        "description": "把每次记忆注入也记进审计（调试用，日志噪音较大）",
        "default": False,
    },
    "memory_min_confidence": {
        "type": "number",
        "label": "最低可信度",
        "description": "低于该可信度的事实不注入（被点名时仍上浮供核对）",
        "default": 0.5,
        "min": 0.0,
        "max": 1.0,
        "step": 0.05,
    },
    "memory_inject_hedge": {
        "type": "boolean",
        "label": "注入时加不确定性说明",
        "description": "中/低置信记忆加「（好像）/（记不太清）」前缀，提醒模型这些是模糊记忆",
        "default": True,
    },
    "memory_max_age_days": {
        "type": "number",
        "label": "记忆最长保留(天)",
        "description": "超过该天数的记忆不再注入；0=不限（数据保留，可被检索到）",
        "default": 0,
        "min": 0,
        "max": 3650,
        "step": 30,
    },
    "memory_on_reset": {
        "type": "select",
        "label": "重置会话时的处理",
        "description": "显式重置（#llm new/exit/stop）时怎么处理该会话的记忆",
        "default": "suspend",
        "options": {
            "suspend": "挂起旧记忆（默认，数据保留）",
            "clear": "清除该对象记忆",
            "keep": "保持不变",
        },
    },
    "memory_upgrade_saved_only": {
        "type": "boolean",
        "label": "重置只保留已保存型",
        "description": "重置挂起时，仅「明确保存/已确认」的记忆继续注入，自动蒸馏的旧事实一律挂起",
        "default": True,
    },
}
