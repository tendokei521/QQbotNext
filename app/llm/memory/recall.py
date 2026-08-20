"""记忆召回内核（v2 / S2）：置信度评分 + 权威降级渲染 + 过滤。

设计：
- 记忆是「对聊天历史的模糊记忆」，不是事实断言；
- 评分融合 置信度×时间衰减 + 确认/多证据加成 + 关键词/提及命中；
- 注入过滤：negative（store 已滤）、过期、超龄、低于置信度阈值、会话重置线（旧记忆挂起）；
- 渲染按置信度分档试探语气：高→陈述、中→（好像）、低→（记不太清）。
"""

from __future__ import annotations

import math
import re
import time
from typing import Any, Iterable

from app.llm.memory.store import _tokenize

# 标题沿用「参考/不准确」的措辞引导（模糊记忆视角）
MEMORY_BLOCK_TITLE = "###供参考（可能不准确）的历史记忆："

# 评分常量
_TOKEN_HIT = 0.5       # query 每命中一个词的分值
_GRAM_HIT = 0.3        # 中文双字 gram 命中分值
_MENTION_BOOST = 0.8   # 提及（@/点名）owner 的加分
_BASE_WEIGHT = 0.15    # 保底系数（乘在 置信度×衰减 上）
_CONFIRMED_BOOST = 0.1
_EVIDENCE_BOOST = 0.05

# 渲染分档
_CONF_HIGH = 0.75
_CONF_MID = 0.5

# “用户明确保存/已确认”型：会话重置（suspend）时仍可注入
_SAVED_SOURCES = ("tool", "deterministic", "correct")

_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


def _is_saved(row: dict) -> bool:
    return int(row.get("confirmed") or 0) == 1 or (row.get("source") or "") in _SAVED_SOURCES


def _query_grams(text: str, win: int = 2) -> set[str]:
    grams: set[str] = set()
    for seg in _CJK_RE.findall(text or ""):
        if len(seg) < win:
            grams.add(seg)
            continue
        for i in range(len(seg) - win + 1):
            grams.add(seg[i : i + win])
    return grams


def _decay(updated_at: int, now: int | None = None) -> float:
    """时间衰减：30 天后衰减到 ~1/2。"""
    now = now or int(time.time())
    age_days = max(0.0, (now - int(updated_at or now)) / 86400.0)
    return 1.0 / (1.0 + age_days / 30.0)


def effective_confidence(row: dict) -> float:
    """记忆的权威置信度（注入分档/过滤用）。"""
    return max(0.0, min(1.0, float(row.get("confidence") or 0.5)))


def score_row(
    row: dict,
    tokens: list[str],
    grams: set[str],
    mention_owners: set[str] | None = None,
    now: int | None = None,
) -> float:
    """综合分 = 置信度×衰减×保底 + 确认/证据加成 + 词/gram 命中 + 提及加成。"""
    conf = effective_confidence(row)
    score = conf * _decay(int(row.get("updated_at") or 0), now) * _BASE_WEIGHT
    if int(row.get("confirmed") or 0):
        score += _CONFIRMED_BOOST
    ev = int(row.get("evidence_count") or 0)
    if ev > 1:
        score += min(math.log1p(ev), 3.0) * _EVIDENCE_BOOST
    hay = (row.get("content") or "") + " " + (row.get("keywords") or "")
    hits = sum(1 for t in tokens if t and t in hay)
    gram_hits = sum(1 for g in grams if g and g in hay)
    score += _TOKEN_HIT * hits + _GRAM_HIT * gram_hits
    if mention_owners and row.get("owner") in mention_owners:
        score += _MENTION_BOOST
    return float(score)


def rank(
    store,
    *,
    owners: Iterable[str],
    query: str = "",
    limit: int = 8,
    max_chars: int = 600,
    mention_owners: Iterable[str] | None = None,
    require_match: bool = True,
    now: int | None = None,
    min_confidence: float = 0.0,
    max_age_days: float = 0.0,
    owner_resets: dict[str, int] | None = None,
    keep_saved_before_reset: bool = True,
) -> list[dict]:
    """回归召回到指定 owners 中的 top-N（含 v2 过滤与评分）。

    - 隔离：owner 限定由调用方传入；
    - 过滤（除非命中提及，直接问某人时允许低置信/旧记忆上浮供核对）：
      过期（expires_at）、超龄（max_age_days）、低于置信度、会话重置线（suspend）;
    - require_match=True（搜索/工具）：未命中默认不返回;
      False（注入）：不排除，仅评分排序（“常驻记忆”）;
    - 输出附 ``_score`` / ``_confidence``，按 score 降序 + 字符预算截断。
    """
    owners = [o for o in owners if o]
    if not owners:
        return []
    mention_set = {o for o in (mention_owners or []) if o}
    merged = list(owners)
    for o in mention_set:
        if o not in merged:
            merged.append(o)

    candidates = store.list_for_owners(merged, limit=200)
    tokens = _tokenize(query) if query else []
    grams = _query_grams(query) if query else set()
    now = now or int(time.time())
    owner_resets = owner_resets or {}

    scored: list[tuple[float, dict]] = []
    has_query = bool(tokens or grams)
    for row in candidates:
        conf = effective_confidence(row)
        mention = row.get("owner") in mention_set
        updated = int(row.get("updated_at") or 0)

        # 已设失效时间且已过期 → 不注入（直接问时可上浮）
        exp = row.get("expires_at")
        if exp and now > int(exp) and not mention:
            continue
        # 超龄
        if max_age_days > 0:
            age_days = max(0.0, (now - updated) / 86400.0)
            if age_days > max_age_days and not mention and not int(row.get("confirmed") or 0):
                continue
        # 置信度阈值（直接问某人时允许上浮供核对）
        if min_confidence > 0 and conf < min_confidence and not mention:
            continue
        # 会话重置线：重置后旧记忆默认挂起，仅「已保存/已确认」型仍注入
        reset_ts = owner_resets.get(row.get("owner") or "", 0)
        if reset_ts and updated < reset_ts and not mention:
            if not keep_saved_before_reset or not _is_saved(row):
                continue

        if require_match and has_query:
            hay = (row.get("content") or "") + " " + (row.get("keywords") or "")
            matched = (
                any(t in hay for t in tokens)
                or any(g in hay for g in grams)
                or mention
            )
            if not matched:
                continue
        sc = score_row(row, tokens, grams, mention_set, now)
        row["_confidence"] = conf
        row["_score"] = sc
        scored.append((sc, row))

    scored.sort(key=lambda x: (-x[0], -int(x[1].get("updated_at") or 0)))

    result: list[dict] = []
    used = 0
    for _sc, row in scored:
        content = str(row.get("content") or "").strip()
        if not content:
            continue
        result.append(row)
        used += len(content) + 2
        if len(result) >= limit or used >= max_chars:
            break
    return result


def render_block(
    hits: Iterable[dict],
    title: str = MEMORY_BLOCK_TITLE,
    hedge: bool = True,
) -> str:
    """渲染注入块。hedge=True 时按置信度加试探前缀：

    高（≥0.75）陈述；中（0.5~0.75）（好像）；低（<0.5）（记不太清）。
    """
    lines = [title, ""]
    for row in hits:
        content = str(row.get("content") or "").strip()
        if not content:
            continue
        prefix = ""
        if hedge:
            conf = effective_confidence(row)
            if conf >= _CONF_HIGH:
                prefix = ""
            elif conf >= _CONF_MID:
                prefix = "（好像）"
            else:
                prefix = "（记不太清）"
        lines.append(f"- {prefix}{content}")
    if len(lines) <= 2:
        return ""
    return "\n".join(lines)


def filter_owned(rows: Iterable[dict], owners: Iterable[str]) -> list[dict]:
    """只保留指定 owner 的行（命令/管理用，防御越权删除）。"""
    allowed = set(owners)
    return [r for r in rows if r.get("owner") in allowed]
