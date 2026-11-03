"""行为段渲染工具：本轮能力探测（caps）与段落拼接。

## 文案去哪了

两套系统提示词（chat / roleplay）的**全部段落文案**都集中在
``app/agent/prompts/presets.py``。本模块只保留与文案无关的机制：

- ``detect_caps``：探测本轮能力（工具 / 视觉 / 发言通道 / 参与度），段落据此裁剪句子；
- ``render``：按调用方给的段落表逐段渲染、拼接（单段失败跳过并留痕）。

## 为什么这么拆

文案会持续打磨、需要两套并排对照；能力探测与拼接是稳定机制。两者放在同一个文件时，
"改一句话"和"改判定逻辑"会混进同一个 diff。

## 纪律（与文案侧共享）

1. **段落里点名的工具必须真实存在**。每个段落函数都接收 ``caps``（见 ``detect_caps``），
   某项能力本轮不可用时**整句消失**——否则模型会去调不存在的工具，或者误以为能力被禁用。
2. **一句话只在一个地方说**。与其它提示词块（主动性 / 消息格式说明）重复的行由调用方
   去重，不要两边各写一遍（"同一主题两处引导会左右脑互搏"）。

## 措辞纪律：不承诺运行时没做的事

行为段的措辞只能描述**此刻真的会发生**的事，不能承诺运行时没做的事（例如本项目
尚未实现的「已读/未读」分节）。等能力落地再改文案，而不是让模型去上下文里找不存在的标题。
"""

from __future__ import annotations

from typing import Any

from app.core.logger import logger

#: 参与度档位（与 `app/agent/participation.LEVELS` 同源；这里只用于取值归一化）
_PARTICIPATION_LEVELS: tuple[str, ...] = ("very_low", "low", "medium", "high", "very_high")

# ==================== 能力探测 ====================

#: 能力键 → 能体现该能力的工具名（任一存在即算可用）
_TOOL_CAPS: dict[str, tuple[str, ...]] = {
    "has_send": ("send_message",),
    "has_finish": ("finish",),
    "has_poke": ("send_poke", "group_poke", "friend_poke"),
    "has_emoji_react": ("set_msg_emoji_like",),
    "has_search": ("tavily_search",),
    "has_expand": ("get_context",),
    "has_expand_user": ("get_context",),
    "has_image": ("expand_image",),
    "has_schedule": ("schedule_task",),
    "has_knowledge": ("knowledge_search",),
}


def _cfg(config: Any, key: str, default: Any = None) -> Any:
    """读配置（缺 config / 配置对象异常时返回 default）。"""
    if config is not None and hasattr(config, "get"):
        try:
            return config.get(key, default)
        except Exception:  # noqa: BLE001 —— 配置读取失败不该让整段提示词炸掉
            return default
    return default


def _has_tool(tools: set[str] | None, names: tuple[str, ...]) -> bool:
    """本轮是否有该能力。

    ``tools is None`` 表示"未知"（调用方没传），按**可用**处理——与
    ``prompt.build_proactive_instruction`` 的既有口径一致：宁可在未知时多说一句，
    也不要在未知时把能力说明整段吞掉。
    """
    if tools is None:
        return True
    return any(name in tools for name in names)


def detect_caps(
    available_tools: set[str] | None = None,
    config: Any = None,
    modalities: list[str] | None = None,
) -> dict:
    """探测本轮能力：段落据此决定哪些句子出现。

    返回的键同时给测试用（逐条断言"能力关掉 → 对应句子消失"）。
    """
    tools = set(available_tools) if available_tools is not None else None
    caps: dict[str, Any] = {
        key: _has_tool(tools, names) for key, names in _TOOL_CAPS.items()
    }
    caps["has_memory"] = tools is None or any(n.startswith("memory_") for n in tools)
    caps["has_any_tool"] = tools is None or bool(tools)

    # 视觉：模型声明的模态说了算；未声明（None=旧行为）时看总开关
    try:
        from app.domain.modalities import supports_image

        vision = supports_image(modalities) if modalities is not None else bool(
            _cfg(config, "image_understanding_enable", True)
        )
    except Exception:  # noqa: BLE001
        vision = True
    caps["vision"] = vision

    # 发言通道：与 ``speech.speech_via_tool`` 同一判定（只看开关），
    # 否则会出现"提示词说文本不会发出去、实际文本就是发出去"的自相矛盾。
    try:
        from app.agent.delivery.speech import speech_via_tool

        speech_by_tool = speech_via_tool(config)
    except Exception:  # noqa: BLE001
        speech_by_tool = bool(_cfg(config, "speech_via_tool", False))
    caps["speech_by_tool"] = speech_by_tool
    caps["outbound_directive"] = bool(_cfg(config, "outbound_directive_enable", True))
    # 引用/@ 的输出通道：工具通道与行内指令通道二选一
    caps["quote_channel"] = speech_by_tool or caps["outbound_directive"]
    # 参与度：**只认新键** `participation_level`（5 档，成本门用的同一份值）。
    # 旧键 `participation`（3 档、默认 medium）已随 llm_prompt 精简退役——两套"参与度"
    # 并存时会出现「人格段说能接就接、成本门却根本不请求」的自相矛盾（线上实测过）。
    raw_level = str(_cfg(config, "participation_level", "medium") or "medium").strip().lower()
    caps["participation"] = raw_level if raw_level in _PARTICIPATION_LEVELS else "medium"
    # 表情活跃度开关已删除（2026-11-01 精简）：固定按"普通频率"渲染
    caps["emoji_level"] = 1
    return caps


# ==================== 渲染 ====================


def render(
    runtime: Any = None,
    available_tools: set[str] | None = None,
    config: Any = None,
    modalities: list[str] | None = None,
    segments: tuple[tuple[str, Any], ...] = (),
) -> str:
    """按段落表渲染整段行为规则（一条 system 的正文）。

    ``segments`` 由提示词方案提供（``prompts.presets.segments_of``）；空表返回空串
    ——调用方应显式传入，缺省值只为兼容"没有方案"的库层直接调用。
    ``runtime`` 只在 ``config`` 缺省时用来取配置，其余不读运行时状态。
    """
    cfg = config if config is not None else getattr(runtime, "config", None)
    caps = detect_caps(available_tools, cfg, modalities)
    parts: list[str] = []
    for name, fn in segments:
        try:
            text = fn(caps)
        except Exception as e:  # noqa: BLE001 —— 单段失败不能拖垮整份提示词
            # 但**不能静默**：段落凭空消失会让"拟人化变差"变成一个查不到原因的现象
            logger.warning(f"[PersonaRules] 段落 {name} 渲染失败（已跳过）: {e}")
            continue
        if text and str(text).strip():
            parts.append(str(text).strip())
    return "\n\n".join(parts)
