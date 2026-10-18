"""发言通道：把「说什么」从「文本输出」变成一次显式动作。

## 为什么需要

此前「模型输出的文本就是发言」：只要它生成文字，消息就必然发出。于是
**「看在眼里但决定不接话」在架构上不可能**——模型只能靠"少写点"来伪装沉默，
而空文本还会被兜底话术填成「抱歉，我暂时无法回答，请稍后再试。」（最不拟人的一句话）。

对齐外部拟人化实现的做法：文本只当思考，发言必须调用 ``send_message``；
这一轮不想说话就什么都不调（或调 ``finish`` 留一句理由）——静默是**正常结束**。

## 一个回合的完整生命周期

```
LLM 输出文本（思考，不发送）
   ├─ send_message(...) → 入 outbox
   ├─ finish(summary)   → 标记收尾
   └─ 其它工具           → 结果回传，可继续下一轮
round 边界：outbox 里的气泡按顺序真发（走 pipeline 原有发送路径 + 钩子），
            每发一条写一条 assistant 历史（不是整轮合并）
循环结束：outbox 空 = 静默（不发、不写历史、记一次"看过没说话"）
```

## 本模块只负责「记账」，不负责「发送」

``SpeechOutbox`` 是纯数据 + 纯函数，工具只往里入稿；真正的发送由 chat/pipeline 完成。
这样做的原因：发送路径上挂着 ``pre_send``/``post_send`` 钩子、发送间隔策略、
``[reply]``/``[@QQ]`` 出口指令、``mark_last_assistant_message_id``、group_log 的
``@send_hook``（"我的发言"入账）——**任何新的发送实现都会漏掉其中一半**。
所以发言通道复用的是同一条 HTTP 出口，只是决定"发什么、什么时候发"的人从
"模型的自然语言输出"换成了"模型显式调用的工具"。

## 三条防呆

1. **上限**：一轮最多 ``max_bubbles`` 条、每条最多 ``max_chars`` 字——防"复读机式刷屏"；
2. **不许假装发送**：入稿结果明确告诉模型"会按顺序发出"，避免它改用文本再"说一遍"；
3. **不重复入稿**：同一句在同一轮里重复调用会被去重（模型常见的"补一句"冲动）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.llm.tool import ToolSpec

#: 一轮最多发几条（对齐行为段的"普通对话 1 条、最多 2 条、故事才 2~4 条"）
DEFAULT_MAX_BUBBLES = 4
#: 单条气泡的字数上限（QQ 单条消息太长会被折叠，也不像人说话）
DEFAULT_MAX_BUBBLE_CHARS = 300
#: 剩余轮次提醒阈值：剩这么多轮还没开口就催一次
DEFAULT_REMINDER_ROUNDS = 3

#: 「系统提醒」文本：必须在**没有发言**时才注入，否则等于催已经说过话的人再说一遍。
#: 用 user 角色——Anthropic/Gemini 适配器会把中途的 system 上提到最前，加了等于没加。
SPEAK_REMINDER = (
    "【系统提醒】工具调用轮次只剩 {rounds} 轮。如果你打算回应，请立刻调用 send_message "
    "把话发出去，不要再调用其它工具——轮次耗尽后群友收不到任何内容。"
)
#: 被直接点名（@/拍一拍/私聊）却没开口时的强制重试提醒
FORCE_SPEAK_REMINDER = (
    "【系统提醒】对方正在直接叫你（@你/戳你/私聊你），这一轮你必须发一条消息回应"
    "（调用 send_message）；实在不想说话也应给一句短的（如「干嘛」「……」「等下」）。"
)

#: 入稿成功的回执：只陈述事实 + 防重复，不再教它怎么写（那是行为段的事）
QUEUED_OK = "ok: 已排队 {count} 条（本轮结束前会按顺序发出）。这就是发送通道本身，不要再用文本重复同一句。"
#: 触到上限的回执
QUEUED_LIMIT = (
    "error: 本轮已排队 {count}/{limit} 条，不再接受新消息。"
    "把想说的留到下一轮，或先调用 finish 收尾。"
)

#: ``SpeechOutbox.queue`` 的三种结局（字符串常量，避免 bool 把"去重"算成"入稿"）
QUEUE_OK = "queued"
QUEUE_DUP = "duplicate"
QUEUE_REJECTED = "rejected"


@dataclass
class SpeechBubble:
    """一条待发气泡：正文 + 可选的引用/点名目标。"""

    text: str
    reply_to: str = ""
    at: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"text": self.text, "reply_to": self.reply_to, "at": list(self.at)}


@dataclass
class SpeechOutbox:
    """一轮发言的记账本。

    - ``bubbles``：当前**待发**队列（``take()`` 之后清空，模拟"发出去就没了"）；
    - ``finished`` / ``finish_reason``：模型是否显式收尾；
    - ``spoken``：本轮是否**曾经**发过言（``take()`` 过至少一条），静默判定用它；
    - ``dropped``：被去重/超限/超长丢弃的条数（贴日志用，不打扰模型）。
    """

    max_bubbles: int = DEFAULT_MAX_BUBBLES
    max_chars: int = DEFAULT_MAX_BUBBLE_CHARS
    bubbles: list[SpeechBubble] = field(default_factory=list)
    finished: bool = False
    finish_reason: str = ""
    active_topic: str = ""
    spoken: int = 0
    dropped: int = 0
    _seen: set[str] = field(default_factory=set, repr=False)

    # ---------- 写入 ----------

    def queue(
        self,
        text: str,
        *,
        reply_to: str = "",
        at: tuple[str, ...] = (),
    ) -> tuple[str, str]:
        """入稿一条，返回 ``(状态, 回执文本)``。

        状态取值（用字符串而不是 bool：入稿、去重、被拒是**三种**不同结局，
        用 bool 会把"去重"算成"入稿"，进而让"已排队 N 条"的计数说谎）：

        - ``"queued"``：真的进了队列；
        - ``"duplicate"``：同一句本轮已入稿，跳过（不算入稿、也不算失败）；
        - ``"rejected"``：空正文 / 超出条数上限。

        超长正文**裁剪**而不是拒绝：拒绝会让模型反复重试同一条。
        """
        clean = str(text or "").strip()
        if not clean:
            return QUEUE_REJECTED, "error: messages 里的正文是空的，请传入要说的话。"
        if len(self.bubbles) >= max(1, int(self.max_bubbles)):
            return QUEUE_REJECTED, QUEUED_LIMIT.format(
                count=len(self.bubbles), limit=self.max_bubbles
            )
        if clean in self._seen:
            self.dropped += 1
            return QUEUE_DUP, "ok: 这句本轮已经排队过了，已跳过重复的一条。"
        if len(clean) > self.max_chars:
            clean = clean[: self.max_chars]
        self._seen.add(clean)
        self.bubbles.append(SpeechBubble(text=clean, reply_to=str(reply_to or ""), at=tuple(at)))
        return QUEUE_OK, ""

    def queue_many(
        self,
        texts: list[str],
        *,
        reply_to: str = "",
        at: tuple[str, ...] = (),
    ) -> tuple[int, list[str]]:
        """批量入稿（``messages`` 传数组时）。返回 ``(入稿条数, 附注列表)``。"""
        accepted = 0
        notes: list[str] = []
        for raw in texts:
            status, message = self.queue(raw, reply_to=reply_to, at=at)
            if status == QUEUE_OK:
                accepted += 1
            elif message:
                notes.append(message)
            if len(self.bubbles) >= max(1, int(self.max_bubbles)):
                break
        return accepted, notes

    def mark_finished(self, summary: str = "", active_topic: str = "") -> None:
        self.finished = True
        self.finish_reason = str(summary or "").strip()[:200]
        self.active_topic = str(active_topic or "").strip()[:60]

    # ---------- 读出 ----------

    def take(self) -> list[SpeechBubble]:
        """取走当前待发队列（round 边界 flush 用；取走即清空）。"""
        taken = list(self.bubbles)
        self.bubbles.clear()
        self.spoken += len(taken)
        return taken

    @property
    def queued(self) -> int:
        """还有几条没发出去。"""
        return len(self.bubbles)

    @property
    def silent(self) -> bool:
        """本轮是否**一句话都没说**（静默 = 可接受的结局，不是失败）。"""
        return self.spoken == 0 and not self.bubbles

    def summary(self) -> dict:
        """一轮的记账摘要（日志/遥测用）。"""
        return {
            "spoken": self.spoken,
            "finished": self.finished,
            "finish_reason": self.finish_reason,
            "active_topic": self.active_topic,
            "dropped": self.dropped,
            "silent": self.silent,
        }


def normalize_messages(raw: Any) -> list[str]:
    """把 ``messages`` 参数归一化成字符串列表（字符串=一条，数组=多条）。

    只是搬运：**不**按空格/标点切句。切句是"要不要分条"的决定，属于模型，
    框架切出来的碎片永远不像人说话（旧实现的 ``split_sentences`` 正是这个毛病）。
    """
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    if isinstance(raw, (list, tuple)):
        out: list[str] = []
        for item in raw:
            if isinstance(item, str):
                if item.strip():
                    out.append(item)
            elif item is not None:
                text = str(item)
                if text.strip():
                    out.append(text)
        return out
    if raw is None:
        return []
    text = str(raw)
    return [text] if text.strip() else []


def _normalize_at(raw: Any) -> tuple[str, ...]:
    """``at_user_id`` 归一化：支持单值 / 数组 / 逗号分隔，只留数字 QQ。"""
    items: list[str] = []
    if isinstance(raw, (list, tuple)):
        items = [str(x) for x in raw]
    elif raw not in (None, ""):
        items = [part for part in str(raw).replace("，", ",").split(",")]
    result: list[str] = []
    for item in items:
        qq = item.strip()
        if qq.isdigit() and qq not in result:
            result.append(qq)
    return tuple(result)


def speech_via_tool(config: Any, *, stream_output: bool | None = None) -> bool:
    """本轮是否启用「文本即思考」的发言通道。

    三个条件同时成立才启用：

    1. 组装档位是 ``qq_agent``（``legacy`` 档行为逐字不变，可一键回退）；
    2. ``speech_via_tool`` 打开；
    3. 走流式输出——**非流式路径没有"轮次边界"可 flush**，硬开会变成
       "想很多最后只发一句"，不如明确按文本即发言处理（调用方会留日志）。
    """
    from app.llm.prompt import is_qq_agent

    if not is_qq_agent(config):
        return False
    if config is None or not hasattr(config, "get"):
        return False
    try:
        if not bool(config.get("speech_via_tool", False)):
            return False
        if stream_output is None:
            stream_output = bool(config.get("stream_output", False))
    except Exception:  # noqa: BLE001 —— 配置异常时退回旧的文本即发言
        return False
    return bool(stream_output)


def build_speech_tools(outbox: SpeechOutbox) -> list[ToolSpec]:
    """构造 ``send_message`` / ``finish`` 两个工具。

    注意 ``source="speech"``：``tool.make_executor`` 会据此跳过「回应要求」追加——
    已经用工具说过话的一轮，再被要求"用自己口吻回应用户"只会让它重复一遍。
    """

    async def _send_message(ctx, args: dict) -> str:
        args = args or {}
        texts = normalize_messages(args.get("messages"))
        if not texts:
            return "error: messages 不能为空：传字符串=发一条，传字符串数组=连发多条。"
        reply_to = str(args.get("reply_to_message_id") or "").strip()
        ats = _normalize_at(args.get("at_user_id"))
        accepted, notes = outbox.queue_many(texts, reply_to=reply_to, at=ats)
        if accepted == 0:
            # 全部被拒/被去重：把原因原样回传（模型据此修正，而不是以为说完了）
            return "\n".join(notes) or "error: 没有可发送的正文。"
        reply = QUEUED_OK.format(count=accepted)
        if len(texts) > accepted:
            reply += f"\n（另 {len(texts) - accepted} 条未入稿：已到本轮条数上限，或与本轮某条重复）"
        return reply

    async def _finish(ctx, args: dict) -> str:
        args = args or {}
        outbox.mark_finished(
            summary=str(args.get("summary") or ""),
            active_topic=str(args.get("active_topic") or ""),
        )
        if outbox.queued:
            return f"ok: 已记录收尾（另有 {outbox.queued} 条已排队，会在本轮结束前发出）。"
        return "ok: 已记录「这一轮不发言」。这是正常结束方式，不需要再调用工具。"

    return [
        ToolSpec(
            name="send_message",
            description=(
                "把话发到当前聊天。**这是你唯一的发言通道**：你输出的文本只是思考，"
                "不会发到聊天里，不调用它群友什么也收不到。\n"
                "用法要点：\n"
                "- messages：字符串=发一条；字符串数组=连发多条。普通对话 1 条就够、最多 2 条，"
                "讲故事/回忆/补刀才 2~4 条；\n"
                "- 数组里每个字符串是一条**完整**消息：不要把同一句话拆到两条里，也不要用空格断句；\n"
                "- reply_to_message_id：只在「我这条是在回哪一条」需要点明时传（上下文里消息前的 #数字）；\n"
                "- at_user_id：只在需要点名某人才传（对方 QQ 号）；引用已经够清楚时不要再 @；\n"
                "- 正文里不要写 [reply]/[@QQ] 这类标记，用参数表达；\n"
                "- 想好了就说，不必先解释、也不要汇报「我已经回复了」。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "messages": {
                        "description": "要说的话：字符串=一条，字符串数组=多条（每条是一条完整消息）",
                        # 用联合类型而不是 anyOf：部分 Provider/网关会拒绝嵌套的 anyOf，
                        # 而联合 type 在各家 tool schema 转换里都被接受；具体形状由
                        # normalize_messages 容错（字符串/数组/其它都能归一化）。
                        "type": ["string", "array"],
                        "items": {"type": "string"},
                    },
                    "reply_to_message_id": {
                        "type": "string",
                        "description": "要引用回复的消息 id（可省略；上下文里形如 #22350 的数字）",
                    },
                    "at_user_id": {
                        "type": "string",
                        "description": "要 @ 的 QQ 号（可省略；多个用逗号分隔）",
                    },
                },
                "required": ["messages"],
            },
            handler=_send_message,
            source="speech",
            scopes=("*",),
            category="发言通道",
        ),
        ToolSpec(
            name="finish",
            description=(
                "表示这一轮到此为止。\n"
                "- 看完不打算说话就调用它（summary 写一句给自己的理由，例如「他们在聊游戏，我没兴趣」）；\n"
                "- 说完话了想收尾也可以调用；\n"
                "- 不调用同样代表结束，但调用能让系统分清「看过决定不说」和「还没说完」。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "一句话写下你这一轮的决定/理由（给自己看）",
                    },
                    "active_topic": {
                        "type": "string",
                        "description": "可省略：当前群里在聊的话题（给下一轮做参考）",
                    },
                },
                "required": [],
            },
            handler=_finish,
            source="speech",
            scopes=("*",),
            category="发言通道",
        ),
    ]
