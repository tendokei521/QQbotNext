"""装配的**五层模型**：一次请求按固定顺序拼成 5 层，每层有名字、有来源。

## 为什么要有"层"

改造前只有一条扁平的段表（12 段按 ``order`` 排），于是：
- 看不出"这段是规则、那是人设、那又是插件塞的"；
- 想调整相对顺序只能改数字，改一个数字全表的意义都变了；
- 排障时 ``describe()`` 只能看到"第几段多长"，答不出"这条 prompt 由哪几层组成"。

层模型把段归类到 5 个语义层，顺序由**层的顺序**决定（层内仍按段的 ``order``）：

| 层 | 中文 | 内容 | 典型段 |
|---|---|---|---|
| ``system_prompt`` | L1 系统提示词 | 规则/协议：安全规则、工作方式、表达、定时/主动性/消息格式 | behavior / schedule / proactive / message_meta |
| ``persona`` | L2 人格预设 | 「我是谁」——唯一来源是人格预设 | persona |
| ``injected`` | L3 钩子注入 | 插件与本轮动态信息：记忆、知识库、群记录、指代、表情标签… | emoji_hint / skills / memory / background / group_log / extra |
| ``history`` | L4 对话历史 | 会话历史（窗口/压缩/开场对话） | history |
| ``user`` | L5 本轮消息 | 本轮正文 + 元信息 + 引用 + 图片 | user |

## 两条硬约束（架构测试锁定）

1. **层序不可乱**：L1 必须在 L2 之前（规则压住人设里手写的输出格式条款），
   L2 必须在 L3 之前（人设是"我是谁"，插件信息是"我现在知道什么"）；
2. **L1 只产出一条 system**：同层规则被合并成一条（见 ``merge_layer_blocks``），
   既省一次"这条 system 算不算前缀"的歧义，也让缓存前缀稳定。
"""

from __future__ import annotations

from typing import Any

# ==================== 层名 ====================

LAYER_SYSTEM_PROMPT = "system_prompt"
LAYER_PERSONA = "persona"
LAYER_INJECTED = "injected"
LAYER_HISTORY = "history"
LAYER_USER = "user"

#: 由外到内的固定顺序（L1 → L5）
LAYERS: tuple[str, ...] = (
    LAYER_SYSTEM_PROMPT,
    LAYER_PERSONA,
    LAYER_INJECTED,
    LAYER_HISTORY,
    LAYER_USER,
)

#: 展示名（``describe()`` / ``[Prompt]`` 日志 / 文档共用一份）
LAYER_LABELS: dict[str, str] = {
    LAYER_SYSTEM_PROMPT: "L1 系统提示词",
    LAYER_PERSONA: "L2 人格预设",
    LAYER_INJECTED: "L3 钩子注入",
    LAYER_HISTORY: "L4 对话历史",
    LAYER_USER: "L5 本轮消息",
}

#: 层序（数字越小越靠前）。段排序先用它，再用段自己的 ``order``。
LAYER_ORDER: dict[str, int] = {layer: index for index, layer in enumerate(LAYERS)}

#: L1 内部段的展示顺序（合同一层的段合并成一条 system 时按它拼）
SYSTEM_PROMPT_SECTIONS: tuple[str, ...] = ("behavior", "schedule", "proactive", "message_meta", "safety_recap")

#: 内置段 → 层。未登记的段（插件注册的）默认归 L3 钩子注入。
SECTION_LAYERS: dict[str, str] = {
    "behavior": LAYER_SYSTEM_PROMPT,
    "schedule": LAYER_SYSTEM_PROMPT,
    "proactive": LAYER_SYSTEM_PROMPT,
    "message_meta": LAYER_SYSTEM_PROMPT,
    "safety_recap": LAYER_SYSTEM_PROMPT,
    "persona": LAYER_PERSONA,
    "emoji_hint": LAYER_INJECTED,
    "skills": LAYER_INJECTED,
    "memory": LAYER_INJECTED,
    "background": LAYER_INJECTED,
    "group_log": LAYER_INJECTED,
    "extra": LAYER_INJECTED,
    "history": LAYER_HISTORY,
    "user": LAYER_USER,
}

#: 未登记段的默认层（插件段都算"钩子注入"）
DEFAULT_LAYER = LAYER_INJECTED


def normalize_layer(value: Any) -> str:
    """层名规整：未知/空值一律按 L3（宁可多显示一段，也不要因为拼错把段丢掉）。"""
    text = str(value or "").strip().lower()
    return text if text in LAYER_ORDER else DEFAULT_LAYER


def layer_of(section: Any) -> str:
    """段所属的层：段自己声明的 ``layer`` 优先，否则按内置映射，最后回落 L3。"""
    declared = getattr(section, "layer", "") or ""
    if declared:
        return normalize_layer(declared)
    name = str(getattr(section, "name", "") or "")
    return SECTION_LAYERS.get(name, DEFAULT_LAYER)


def layer_index(layer: str) -> int:
    return LAYER_ORDER.get(normalize_layer(layer), LAYER_ORDER[DEFAULT_LAYER])


def layer_label(layer: str) -> str:
    return LAYER_LABELS.get(normalize_layer(layer), LAYER_LABELS[DEFAULT_LAYER])


def sort_key(section: Any) -> tuple[int, int, str]:
    """段的排序键：先层序，再段自己的 ``order``，最后名字（同输入必然同顺序）。"""
    return (layer_index(layer_of(section)), int(getattr(section, "order", 0) or 0), str(getattr(section, "name", "")))


def summarize_layers(rows: list[dict]) -> str:
    """把 ``describe()`` 的行压成一行层摘要，供 ``[Prompt]`` 日志打头。

    形如：``L1 系统提示词: behavior(3540)+schedule(396) | L2 人格预设: persona(1665)``；
    空段（``role == "-"``）不列，避免摘要里全是 0。
    """
    buckets: dict[str, list[str]] = {layer: [] for layer in LAYERS}
    for row in rows or []:
        if row.get("role") == "-" or not row.get("chars"):
            continue
        layer = normalize_layer(row.get("layer"))
        buckets[layer].append(f"{row.get('block')}({row.get('chars')})")
    parts = [f"{LAYER_LABELS[layer]}: " + "+".join(items) for layer, items in buckets.items() if items]
    return " | ".join(parts)
