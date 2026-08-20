"""确定性“记住”兜底（P2）：不依赖模型是否调工具，检测到记忆指令直接入库。

对齐 ``scheduler.has_schedule_intent`` 的兜底风格。规则保守：
- 含记忆触发词 + 能抽出 ≥2 字符的非疑问句主句才入库；
- 疑问句（…吗 / …？）一律跳过，避免把“还记得我吗？”存成记忆。
"""

from __future__ import annotations

import re

# 触发词列表（选择逻辑：取原文中最先出现的那个，从而保留完整主句）
_AUTOSAVE_TRIGGERS = (
    "请记住", "别忘了", "别忘记", "记住", "记住了",
    "我喜欢", "我讨厌", "我不喜欢", "我爱", "我最喜欢", "我最爱",
    "我是", "我叫", "我住在", "我住", "我的生日", "我的名字",
)

_LEAD_FILLERS = re.compile(r"^[好的嗯哦噢额啊哈]+\s*")
_TAIL_PUNCT = " ，,。！!?？的呀哦呃了"
# 疑问词：抽出的“记忆”若含这些词，多半是问题而非事实，禁止入库
_INTERROGATIVE = ("什么", "谁", "哪", "怎么", "为什么", "如何", "吗", "呢", "？", "?")


def autosave_clause(text: str) -> str:
    """从用户消息抽出要长期记住的主句；抽不出 / 命中疑问返回空串。

    例：
      "记住我喜欢喝美式"        → "我喜欢喝美式"
      "帮我记住我叫小明"        → "我叫小明"
      "我是前端工程师"         → "前端工程师"
      "还记得我吗？"           → ""（疑问）
      "我喜欢什么"            → ""（疑问，不误存“什么”）
    """
    t = (text or "").strip()
    if not t or len(t) > 120:
        return ""
    if t.endswith(("？", "?", "吗", "吗？")):
        return ""
    t = _LEAD_FILLERS.sub("", t)

    best: tuple[int, str] | None = None
    for cand in _AUTOSAVE_TRIGGERS:
        found = t.find(cand)
        if found >= 0 and (best is None or found < best[0]):
            best = (found, cand)
    if best is None:
        return ""
    idx, trig = best

    rest = t[idx + len(trig):].strip(_TAIL_PUNCT).strip()
    if not rest:
        return ""
    clause = rest.strip(_TAIL_PUNCT).strip()
    if len(clause) < 2:
        return ""
    if any(w in clause for w in _INTERROGATIVE):
        return ""
    return clause[:120]


def wants_autosave(text: str) -> bool:
    """是否有值得自动入库的记忆指令。"""
    return bool(autosave_clause(text))
