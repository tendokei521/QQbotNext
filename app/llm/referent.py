"""指代消解（设计基准：``docs/referent-resolution-design.md``）。

要解决的问题（真实日志 group_466052056 / 2026-09-18 21:37）：用户说
「那你能做到消息里的那个样子吗」——**纯回指句**，没有 id、没有引用段。模型手上只有背景块里
平铺的 8 个 ``【未展开:…】``，于是挑了一个"最显眼"的（群里最新那条转发），答非所问。

本模块做三件事（全部是**确定性**的，不让模型自省）：

1. **判定**：这句话是显式指向（回复/@）、按位置指代（上一条/刚才那条）、纯回指（那个样子）、
   还是人物指代；
2. **决策**：确定 → 请求前直接预取；候选不止一个 → 按 ``referent_ambiguous_policy``
   （推荐 ``fetch_all``：都取回，让模型按内容判断，不反问也不猜）；
3. **组装**：把「当前对话焦点 + 指代解析说明 + 已预取内容」拼成一块 system 文本，
   交给 ``chat`` 前置进 ``pre_history_text``。

设计红线：能由代码算出来的（回复段、@、展开历史、时间邻近）绝不交给模型决策。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.core.logger import logger
from app.llm import focus

# 按位置指代："上一条/刚才那条/消息里的那个"
_RELATIVE_RE = re.compile(
    r"(上一条|上条|刚才那条|刚才的消息|刚才那条消息|刚刚那条|那条消息|这条消息|"
    r"最新一条|上面那条|上面那条消息|那个消息|刚才发的|刚发的|消息里)"
)
# 纯回指：没有新实体，只能靠焦点
_ANAPHORA_RE = re.compile(
    r"(那个样子|那样|那种|那个|这条|那条|这个|这些|刚才说的|你刚说的|你说的|上一个|它)"
)
# 人物指代
_PERSON_RE = re.compile(r"(他是谁|她是谁|这个人|那个人|是谁呀|是谁啊|谁啊)")
# 文本里显式出现的 id（5 位以上数字，通常是 QQ 号或消息 id）
_EXPLICIT_ID_RE = re.compile(r"\b(\d{5,})\b")

# 预取块**不设长度上限**：这里装的就是"要看的正文"，砍掉等于让模型看得更少
# （与 context_tools 的"不截断"策略一致；需要保护上下文时从候选数
#  ``referent_prefetch_max`` 与 ``expand_recent`` 的 count 入手，而不是砍内容）。


@dataclass
class Referent:
    """一个候选指向。"""

    kind: str          # "message" | "user"
    ref: str
    why: str = ""      # 人话理由（写进块里给模型看，也便于日志排查）
    confidence: str = "medium"   # explicit / high / medium
    origin: str = "event"        # event（本轮话语给出的）/ focus（来自焦点表）


@dataclass
class ReferentPlan:
    """本轮指代的判定结果。"""

    kind: str = "none"           # reply / @ / relative / anaphora / person / explicit_id / none
    messages: list[Referent] = field(default_factory=list)
    users: list[Referent] = field(default_factory=list)
    recent_count: int = 0        # >0 表示需要按位置取最近 N 条
    ambiguous: bool = False
    note: str = ""

    @property
    def actionable(self) -> bool:
        return bool(self.messages or self.users or self.recent_count)


def _segments(event: Any) -> list:
    msg = getattr(event, "message", None)
    return msg if isinstance(msg, list) else []


def _seg_field(seg: Any, key: str, default: Any = "") -> Any:
    if isinstance(seg, dict):
        return (seg.get("data") or {}).get(key, default)
    return (getattr(seg, "data", {}) or {}).get(key, default)


def _seg_type(seg: Any) -> str:
    return seg.get("type", "") if isinstance(seg, dict) else getattr(seg, "type", "")


def resolve(
    user_text: str,
    event: Any = None,
    *,
    focus_items: list | None = None,
    self_ids: set[str] | None = None,
    max_candidates: int = 2,
) -> ReferentPlan:
    """判定本轮指代（纯函数，便于单测）。"""
    text = str(user_text or "")
    focus_items = list(focus_items or ())
    self_ids = {str(x) for x in (self_ids or set())}
    plan = ReferentPlan()

    # 1. 显式回复：最强证据，直接定目标
    for seg in _segments(event):
        if _seg_type(seg) != "reply":
            continue
        ref = str(_seg_field(seg, "id", "") or "")
        if ref:
            plan.kind = "reply"
            plan.messages.append(Referent("message", ref, "你回复的那条消息", "explicit"))
            break

    # 2. @ 对象（人物候选）
    for seg in _segments(event):
        if _seg_type(seg) != "at":
            continue
        qq = str(_seg_field(seg, "qq", "") or "")
        if qq and qq not in ("all", "0") and qq not in self_ids:
            plan.users.append(Referent("user", qq, "本轮被 @ 的人", "explicit"))

    # 3. 文本里显式写出的 id（用户经常直接把消息 id 贴出来）
    for ref in _EXPLICIT_ID_RE.findall(text):
        if ref in self_ids:
            continue
        if not any(c.ref == ref for c in plan.messages):
            plan.messages.append(Referent("message", ref, "消息里直接给出的 id", "high"))

    focus_messages = [i for i in focus_items if i.kind == "message"]
    focus_users = [i for i in focus_items if i.kind == "user"]

    # 4. 人物指代：焦点里的人优先，其次本轮发送者
    if _PERSON_RE.search(text):
        for item in focus_users[:max_candidates]:
            if not any(c.ref == item.ref for c in plan.users):
                plan.users.append(Referent("user", item.ref, "疑似的指代对象（焦点里的人）", "medium"))
        if not plan.users:
            sender = str(getattr(event, "user_id", "") or "")
            if sender:
                plan.users.append(Referent("user", sender, "当前说话的人", "high"))
        plan.kind = plan.kind if plan.kind != "none" else "person"

    # 5. 按位置指代：不需要 id，交给 expand_recent
    relative = bool(_RELATIVE_RE.search(text))
    if relative:
        plan.recent_count = max(plan.recent_count, 2)

    # 6. 纯回指：整句没有新实体/新 id，只能靠焦点。
    #    注意"消息里的那个样子"同时命中"按位置"与"回指"两个特征——此时以**焦点优先**
    #    （上一轮讨论过的那条），同时把位置上的最近一条也带上（fetch_all 策略）。
    anaphora = bool(_ANAPHORA_RE.search(text)) and not _EXPLICIT_ID_RE.search(text)
    if anaphora and not plan.messages:
        for item in focus_messages[:max_candidates]:
            phrase = focus.SOURCE_PHRASE.get(item.source, "相关")
            plan.messages.append(Referent(
                "message", item.ref, f"{phrase} {item.display()}", "medium", origin="focus",
            ))
        plan.recent_count = max(plan.recent_count, 1)
        plan.kind = "anaphora" if plan.messages else ("relative" if relative else "anaphora")
    elif relative and plan.kind == "none":
        plan.kind = "relative"

    # 7. 歧义判定：焦点候选与"最近一条"同时存在 → 都取
    if plan.messages and plan.recent_count:
        plan.ambiguous = True
    if plan.messages and len(focus_messages) > len(plan.messages):
        plan.ambiguous = True

    # 说明文本：让模型知道框架是怎么理解的
    bits: list[str] = []
    if plan.messages:
        bits.append("、".join(f"{c.why}" for c in plan.messages[:2]))
    if plan.recent_count:
        bits.append(f"最近 {plan.recent_count} 条消息")
    if bits:
        plan.note = "这句话没有直接指明对象，按“焦点优先 + 位置邻近”理解为：" + "；".join(bits)
    return plan


def _cfg(runtime: Any, key: str, default: Any) -> Any:
    config = getattr(runtime, "config", None)
    try:
        return config.get(key, default) if config is not None else default
    except Exception as e:
        logger.debug(f"[Referent] 读取配置 {key} 失败，使用默认值 {default!r}: {e}")
        return default


def _register_explicit(runtime, event, session_id: str, plan: ReferentPlan, *, ttl: float, max_items: int) -> None:
    """把本轮**话语里给出的**指向登记进焦点表（回复/@/文本 id）。

    来自焦点表的候选（``origin="focus"``）不在此重复登记——它们本来就在表里，
    重复登记还会把弱候选（如窗口项）的 source 升级成"你提到的"，污染显著性排序。
    """
    bot_id = getattr(runtime, "bot_id", "")
    for cand in plan.messages:
        if cand.origin != "event":
            continue
        focus.note(bot_id, session_id, cand.ref, kind="message",
                   label=f"消息{cand.ref}", source="reply" if cand.confidence == "explicit" else "text",
                   ttl=ttl, max_items=max_items)
    for cand in plan.users:
        if cand.origin != "event":
            continue
        focus.note(bot_id, session_id, cand.ref, kind="user",
                   label=f"用户{cand.ref}", source="at", ttl=ttl, max_items=max_items)


async def build_block(
    runtime: Any,
    session_id: str,
    user_text: str = "",
    ctx: Any = None,
    *,
    allow_prefetch: bool = True,
    focus_only: bool = False,
) -> str:
    """组装「焦点 + 指代解析 + 预取内容」块；未启用或无内容时返回空串。

    ``focus_only=True``（主动消息 / 定时任务路径）只给焦点行，不做预取。
    """
    if not _cfg(runtime, "referent_resolve_enable", True):
        return ""
    bot_id = getattr(runtime, "bot_id", "")
    if session_id in (None, ""):
        return ""
    ttl = float(_cfg(runtime, "referent_focus_ttl", focus.DEFAULT_TTL_SECONDS) or focus.DEFAULT_TTL_SECONDS)
    max_items = int(_cfg(runtime, "referent_focus_max", focus.DEFAULT_MAX_ITEMS) or focus.DEFAULT_MAX_ITEMS)

    event = getattr(ctx, "event", None) if ctx is not None else None
    self_ids = {
        str(getattr(runtime, "bot_id", "") or ""),
        str(getattr(event, "self_id", "") or "") if event is not None else "",
        str(getattr(event, "bot_id", "") or "") if event is not None else "",
    }
    self_ids.discard("")

    items = focus.items(bot_id, session_id)
    plan = ReferentPlan()
    if not focus_only and str(user_text or "").strip():
        plan = resolve(
            user_text, event, focus_items=items, self_ids=self_ids,
            max_candidates=max(1, int(_cfg(runtime, "referent_prefetch_max", 2) or 2)),
        )
        _register_explicit(runtime, event, session_id, plan, ttl=ttl, max_items=max_items)
        items = focus.items(bot_id, session_id)

    parts: list[str] = []
    focus_text = focus.focus_lines(bot_id, session_id, limit=3)
    if focus_text:
        parts.append(focus_text)

    fetched_text = ""
    if (
        allow_prefetch
        and not focus_only
        and plan.actionable
        and ctx is not None
        and getattr(ctx, "bot", None) is not None
        and bool(_cfg(runtime, "referent_prefetch_enable", True))
    ):
        policy = str(_cfg(runtime, "referent_ambiguous_policy", "fetch_all") or "fetch_all").lower()
        limit = max(1, int(_cfg(runtime, "referent_prefetch_max", 2) or 2))
        messages = [c.ref for c in plan.messages[:limit]]
        users = [c.ref for c in plan.users[:limit]]
        recent = plan.recent_count
        if policy == "ask" and plan.ambiguous:
            parts.append("（这句可能在指多个对象：先按上面的焦点判断，拿不准就先问用户一句，不要瞎猜。）")
        else:
            if policy == "focus_first" and messages:
                recent = 0  # 只取焦点项
            try:
                from app.llm.context_tools import fetch_entities, fetch_recent

                blocks: list[str] = []
                # 先按位置取最近几条（它会返回涉及的 id），再补取不重复的候选，
                # 避免同一条消息被取两次、也不让它在块里出现两遍。
                covered: set[str] = set()
                if recent:
                    result_recent = await fetch_recent(
                        ctx, count=recent,
                        limit=int(_cfg(runtime, "referent_prefetch_item_chars", 0) or 0),
                    )
                    blocks.extend(result_recent.blocks)
                    if not result_recent.blocks and result_recent.partial:
                        blocks.extend(result_recent.partial)
                    covered = {str(r) for r in result_recent.refs}
                pending_messages = [m for m in messages if m not in covered]
                if pending_messages or users:
                    result = await fetch_entities(
                        ctx, users=users, messages=pending_messages, source="expand",
                    )
                    blocks.extend(result.blocks)
                    if not result.blocks and result.partial:
                        blocks.extend(result.partial)
                if blocks:
                    fetched_text = "（下面这些内容已经替你取好了，直接用来回答即可）\n" + "\n".join(blocks)
            except Exception as e:
                # 预取失败不能影响回复主流程：退回"只给焦点"，由模型自己决定是否调工具
                logger.debug(f"[Referent] 预取失败（已忽略）: {e}")

    if plan.note and not fetched_text:
        parts.append(f"（{plan.note}）")
    if fetched_text:
        if plan.note:
            parts.append(f"（{plan.note}）")
        parts.append(fetched_text)

    block = "\n\n".join(p for p in parts if p)
    return block if block else ""


async def focus_only_block(runtime: Any, session_id: str) -> str:
    """只给焦点行（主动消息 / 定时任务路径）。"""
    return await build_block(runtime, session_id, "", None, allow_prefetch=False, focus_only=True)
