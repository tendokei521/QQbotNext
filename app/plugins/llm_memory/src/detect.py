"""确定性「记住」兜底（不依赖模型是否调用工具）。

规则保守、可解释，和 ``scheduler.has_schedule_intent`` 同风格：
- 只有命中「记忆指令词」（请记住/记住/别忘了…）或「自述事实词」（我喜欢/我叫/我住在…）
  才考虑入库，其余一律跳过；
- 疑问句一律跳过（"还记得我吗？" / "我喜欢什么"），避免把问题存成事实；
- 触发词**保留在内容里**（"我喜欢喝美式" → "我喜欢喝美式"），不再截成半句"喝美式"；
- 否定/反义防护：整句含否定词（不/别/没/无/讨厌…）且触发词是事实型（我是/我叫/
  我住在/我有/我的…）时，保留**原作者整句**，避免截出与用户意思相反的片段；
- 长度上限 120 字；``extract_for_profile`` 额外识别姓名/身份/住址类自述，供 manager
  写 ``kind=profile`` 的画像条目。
"""

from __future__ import annotations

import re
from typing import Any

#: 明确要求「记住」的指令词（命中后丢弃指令本身，只记内容）
_COMMAND_TRIGGERS = ("请记住", "帮我记住", "别忘了", "别忘记", "记住", "记住了")
#: 自述事实词：命中即认为这句话本身值得记（内容保留触发词）
_CONTENT_TRIGGERS = (
    "我喜欢", "我讨厌", "我不喜欢", "我爱", "我最喜欢", "我最爱",
    "我是", "我叫", "我住在", "我住", "我的生日", "我的名字", "我有",
)
#: 事实型触发词：带否定词时要保留整句（截断会反转语义）
_FACT_TRIGGERS = ("我是", "我叫", "我住在", "我有", "我的生日", "我的名字")
#: 画像类触发词：供 extract_for_profile 使用
_PROFILE_TRIGGERS = ("我叫", "我是", "我住在", "我的生日", "我的名字")
#: 误命中画像的词头（"我是说/我是觉得…"只是口头语，不是身份自述）
_PROFILE_FAKE_PREFIXES = (
    "我是说", "我是问", "我是想", "我是觉得", "我是认为",
    "我是开玩笑", "我是逗你", "我是来",
)

#: 疑问词：命中即不当事实（省得把"我喜欢什么"存成记忆）
_QUESTION_WORDS = (
    "什么", "谁", "哪", "怎么", "为什么", "如何", "多少", "几点", "几号",
    "是不是", "有没有", "能不能", "要不要",
)
_QUESTION_TAILS = ("吗", "呢", "？", "?")
#: 否定词：整句出现即触发"事实型保留整句"规则
_NEGATIONS = ("不", "别", "没", "无", "讨厌", "并非", "从未")

#: 开场语气词（"嗯/哦/好的…"）：只在开头剥一次，避免污染记忆内容
_LEAD_FILLERS = re.compile(r"^[\s]*(?:好的|好吧|嗯|哦|噢|额|啊|哈|嘿|哎)+[\s，,。.!！]*")
_WS_RE = re.compile(r"\s+")
#: 首尾无意义标点/语气词
_TAIL_PUNCT = " ，,。！!~～…\t\r\n"
_EDGE_WORDS = ("的", "呀", "哦", "呃", "了")


def _normalize(text: Any) -> str:
    """压缩空白、去首尾（保留标点语义，句末标点在后面判断）。"""
    return _WS_RE.sub(" ", str(text if text is not None else "")).strip()


def _strip_edges(clause: str) -> str:
    """去首尾标点与语气词（不碰句中的否定词）。"""
    value = clause.strip(_TAIL_PUNCT).strip()
    changed = True
    while changed and value:
        changed = False
        for word in _EDGE_WORDS:
            if value.endswith(word) and len(value) > len(word):
                value = value[: -len(word)].strip(_TAIL_PUNCT).strip()
                changed = True
    return value


def _is_question(text: str) -> bool:
    """疑问句判定：疑问词或句末疑问语气（"吗/呢/？"）。"""
    value = str(text or "").strip()
    if not value:
        return False
    if value.endswith(_QUESTION_TAILS):
        return True
    return any(word in value for word in _QUESTION_WORDS)


def _find_trigger(text: str, triggers: tuple[str, ...]) -> tuple[int, str] | None:
    """找最先出现的触发词；同一位置取更长的词（"我住在"优先于"我住"）。"""
    best: tuple[int, str] | None = None
    for trigger in triggers:
        index = text.find(trigger)
        if index < 0:
            continue
        if best is None or index < best[0] or (index == best[0] and len(trigger) > len(best[1])):
            best = (index, trigger)
    return best


def _has_negation(text: str) -> bool:
    """整句是否含否定词。"""
    return any(word in text for word in _NEGATIONS)


def autosave_clause(text: str) -> str:
    """从用户消息抽出要长期记住的主句；抽不出 / 命中疑问返回空串。

    例：
      "记住我喜欢喝美式"     → "我喜欢喝美式"（保留触发词）
      "帮我记住我叫小明"     → "我叫小明"
      "我喜欢喝美式"         → "我喜欢喝美式"
      "记住下周五开会"       → "下周五开会"
      "我不是北京人，我住在上海" → 原文整句（事实型 + 否定）
      "还记得我吗？" / "我喜欢什么" → ""（疑问）
    """
    value = _normalize(text)
    if not value:
        return ""
    value = _LEAD_FILLERS.sub("", value).strip()
    if not value or _is_question(value):
        return ""

    clause = ""
    content_hit = _find_trigger(value, _CONTENT_TRIGGERS)
    if content_hit is not None:
        index, trigger = content_hit
        if trigger in _FACT_TRIGGERS and _has_negation(value):
            # 否定 + 事实型：截断可能反转语义，保留原作者整句
            clause = value
        else:
            clause = value[index:]
    else:
        command_hit = _find_trigger(value, _COMMAND_TRIGGERS)
        if command_hit is not None:
            index, trigger = command_hit
            clause = value[index + len(trigger):]

    clause = _strip_edges(clause)
    if len(clause) < 2 or _is_question(clause):
        return ""
    return clause[:120]


def wants_autosave(text: str) -> bool:
    """是否有值得自动入库的记忆指令。"""
    return bool(autosave_clause(text))


def extract_for_profile(text: str) -> dict | None:
    """识别姓名/身份/住址/生日类自述，返回 ``{"kind": "profile", "content": ...}``。

    识别不到、是疑问句、只有口头语（"我是说…"）或内容过短时返回 None。
    """
    value = _normalize(text)
    if not value:
        return None
    value = _LEAD_FILLERS.sub("", value).strip()
    if not value or _is_question(value):
        return None

    hit = _find_trigger(value, _PROFILE_TRIGGERS)
    if hit is None:
        return None
    index, trigger = hit
    tail = _strip_edges(value[index + len(trigger):])
    if len(tail) < 2:
        return None

    content = value if _has_negation(value) else value[index:]
    content = _strip_edges(content)
    if content.startswith(_PROFILE_FAKE_PREFIXES):
        return None
    if len(content) < 3 or _is_question(content):
        return None
    return {"kind": "profile", "content": content[:120]}
