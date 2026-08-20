"""记忆召回内核（P1）：评分 + 渲染成注入提示词的 system 块。

设计约定：
- 本模块是「召回接缝」——将来上 embedding 只改这里（评分器换成向量精排），
  上层（tools / 注入 / 命令）与存储合同完全不动；
- 隔离由调用方传入的 ``owners`` 决定（绝不在本层越权跨 owner）。
"""

from __future__ import annotations

import re
import time
from typing import Any, Iterable

from app.llm.memory.store import _tokenize

MEMORY_BLOCK_TITLE = "### 长期记忆"

# 评分常量（启发式，可后续调参 / 换向量精排）
_TOKEN_HIT = 0.5       # query 每命中一个词的分值
_GRAM_HIT = 0.3        # 中文双字 gram 命中分值（处理“小明喜欢什么”这类整串）
_MENTION_BOOST = 0.8   # 提及（@/点名）owner 的加成
_BASE_WEIGHT = 0.15    # 无关键词命中时的保底分

_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


def _query_grams(text: str, win: int = 2) -> set[str]:
    """抽查询串里的滑动窗口 grams（CJK 用），并去掉纯等于整串的孤 token。"""
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


def score_row(
    row: dict,
    tokens: list[str],
    grams: set[str],
    mention_owners: set[str] | None = None,
    now: int | None = None,
) -> float:
    """单条记忆的综合分 = 重要度×衰减 + 词/gram 命中 + 提及加成。

    tokens/grams 均为空时退化为 重要度×衰减。
    """
    importance = float(row.get("importance") or 0.0)
    score = importance * _decay(int(row.get("updated_at") or 0), now) * _BASE_WEIGHT
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
) -> list[dict]:
    """回归召回到指定 owners 中的 top-N。

    - 先按 owner 限定取候选（隔离）；
    - 关键词命中从 query 分词得到（提及成员名 token 也在其中）；
    - 提及 owner 的行整体加分；
    - require_match=True（搜索/工具）：未命中默认不返回；
      require_match=False（注入）：不排除，仅靠评分排序，保证“常驻记忆”都在。
    - 输出按 score 降序 + 字符预算截断。
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

    scored: list[tuple[float, dict]] = []
    has_query = bool(tokens or grams)
    for row in candidates:
        if require_match and has_query:
            hay = (row.get("content") or "") + " " + (row.get("keywords") or "")
            matched = (
                any(t in hay for t in tokens)
                or any(g in hay for g in grams)
                or row.get("owner") in mention_set
            )
            if not matched:
                continue
        sc = score_row(row, tokens, grams, mention_set, now)
        scored.append((sc, row))
    scored.sort(key=lambda x: (-x[0], -int(x[1].get("updated_at") or 0)))

    result: list[dict] = []
    used = 0
    for _sc, row in scored:
        content = str(row.get("content") or "").strip()
        if not content:
            continue
        row["_score"] = round(_sc, 4)
        result.append(row)
        used += len(content) + 2
        if len(result) >= limit or used >= max_chars:
            break
    return result


def render_block(hits: Iterable[dict], title: str = MEMORY_BLOCK_TITLE) -> str:
    """把命中记忆渲染进 system 的文本块（无命中返回空串）。"""
    lines = [title, ""]
    for row in hits:
        content = str(row.get("content") or "").strip()
        if content:
            lines.append("- " + content)
    if len(lines) <= 2:
        return ""
    return "\n".join(lines)


def filter_owned(rows: Iterable[dict], owners: Iterable[str]) -> list[dict]:
    """只保留指定 owner 的行（命令/管理用，防御越权删除）。"""
    allowed = set(owners)
    return [r for r in rows if r.get("owner") in allowed]
