"""会话历史模型与唯一渲染器。

## 为什么有这个模块

历史此前是「一条冻结文本」：``enhance.format_user_context`` 把
``(时间：…) / (当前群号：…) / 发送者：X / 发送了：正文`` 拼成一个字符串，
``chat`` 再把它整条写进历史，渲染时套一层前缀了事。后果：

- 基础信息（发送者 / 时间 / 群号）在设计上是结构，实现上被压平进 content，无法重排、
  无法换格式、无法脱敏；
- 渲染有二套实现（会话历史 `format_history_for_llm`、在线背景 `format_online_history`），
  同一件事两处维护；
- 只有在线背景接「已展开登记」，会话历史不接——模型第 1 轮展开的内容第 2 轮又变回占位。

本模块把历史条目的形状固定为**基础信息 + 轮次信息**，并提供唯一渲染器
``render_history_entry``：

```
HistoryEntry
├── base  BaseInfo   基础信息：时间 / 发送者 / 群号 / 正文 / 原始消息段（写时确定）
└── turn  TurnInfo   轮次信息：本轮取回的内容（可增长，见 H4 补全回写）
```

## 渲染约定（与重构前逐字一致，由 tests/test_llm_history_render.py 锁定）

- **单行形态**（会话历史、群聊背景）：``MM-DD HH:MM 昵称(QQ): 正文``；
  bot 自己的消息标为「我」，私聊对方标为「对方」；
- **多行增强块**（内容自带 ``发送者：/发送了：`` 等分节）**原样输出**，不再套外层前缀，
  否则会变成「MM-DD HH:MM 用户X: 发送者：X…」这种重复脏信息；
- assistant 消息**不加任何前缀**（防模型模仿 ``MM-DD HH:MM 我: `` 写进回复污染历史）；
- 换行统一为 ``\\n``：旧数据可能是 ``\\r\\n``，而分节识别按 ``^`` / ``\\n`` 匹配，
  CRLF 会让「增强块」判定失败并多套一层前缀。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# ==================== 常量（与 group_context 同源，迁移期两处保持一致） ====================

SELF_TAG = "我"
PRIVATE_OTHER_TAG = "对方"

#: 内容已自带「发送者/发送了/时间」自描述（LLM 增强块）：原样输出，不再套外层前缀
_ENHANCED_RE = re.compile(
    r"(?:^|\n)(?:发送者：|发送者昵称：|发送了：|消息正文：)|^\(时间："
)

_SENTENCE_LIKE_RE = re.compile(r"[\s，。！？、；：,.!?;:]")
_SENTENCE_LIKE_MAX_LEN = 12


def normalize_newlines(text: Any) -> str:
    """统一换行为 ``\\n``（``\\r\\n`` / ``\\r`` → ``\\n``），避免分节匹配失效。"""
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n")


# ==================== 脱敏 ====================


def safe_nickname(nickname: str, user_id: Any = "") -> str:
    """把句子型/超长昵称脱敏为 ``用户<QQ>``，普通昵称保留原样。

    目的：避免昵称内容（如"学费"）进入 LLM 上下文后被当成对话内容。
    """
    nick = (nickname or "").strip()
    if not nick:
        return f"用户{user_id}" if user_id not in (None, "") else "用户"
    if len(nick) > _SENTENCE_LIKE_MAX_LEN or _SENTENCE_LIKE_RE.search(nick):
        return f"用户{user_id}" if user_id not in (None, "") else "用户"
    return nick


def safe_sender_label(sender: str) -> str:
    """把 ``昵称(QQ)`` 形式的发送者标签脱敏为安全标签。"""
    sender = (sender or "").strip()
    m = re.match(r"^(.*)\((\d+)\)$", sender)
    if m:
        nick, qq = m.group(1), m.group(2)
        safe = safe_nickname(nick, qq)
        if safe == f"用户{qq}":
            return safe
        return f"{safe}({qq})"
    return safe_nickname(sender, "")


def is_enhanced_content(content: Any) -> bool:
    """内容是否自带「发送者/发送了/时间」自描述（增强块）。"""
    return bool(_ENHANCED_RE.search(normalize_newlines(content)))


def sender_label(
    nickname: str,
    user_id: Any = "",
    *,
    include_user_id: bool = True,
    mask_nickname: bool = False,
) -> str:
    """群聊发送者标签：普通昵称保留；``mask_nickname`` 时句子型昵称转为 ``用户<QQ>``。"""
    if mask_nickname:
        nick = safe_nickname(nickname, user_id)
        if nick == f"用户{user_id}" and user_id not in (None, ""):
            return nick
    else:
        nick = (nickname or "").strip()
    parts: list[str] = [nick] if nick else []
    if include_user_id and user_id not in (None, ""):
        parts.append(f"({user_id})" if parts else str(user_id))
    return "".join(parts) if parts else "用户"


def time_prefix(ts: Any) -> str:
    """unix 时间戳 → ``MM-DD HH:MM `` 前缀；非法/缺失返回空串。"""
    if ts in (None, ""):
        return ""
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%m-%d %H:%M ")
    except Exception:  # noqa: BLE001
        return ""


# ==================== 数据模型 ====================


@dataclass
class BaseInfo:
    """基础信息：写时确定、之后不变的部分。"""

    time: int = 0
    sender_id: str = ""
    sender_name: str = ""
    group_id: str = ""
    is_private: bool = False
    text: str = ""
    #: 原始 OneBot 消息段（保留 @/引用/转发/图片，供未来重渲染与"按需展开"定位）
    segments: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        data: dict[str, Any] = {"time": int(self.time or 0)}
        if self.sender_id:
            data["sender_id"] = str(self.sender_id)
        if self.sender_name:
            data["sender_name"] = str(self.sender_name)
        if self.group_id:
            data["group_id"] = str(self.group_id)
        if self.is_private:
            data["is_private"] = True
        if self.text:
            data["text"] = str(self.text)
        if self.segments:
            data["segments"] = list(self.segments)
        return data

    @classmethod
    def from_dict(cls, data: dict | None) -> "BaseInfo":
        data = data or {}
        return cls(
            time=int(data.get("time", 0) or 0),
            sender_id=str(data.get("sender_id", "") or ""),
            sender_name=str(data.get("sender_name", "") or ""),
            group_id=str(data.get("group_id", "") or ""),
            is_private=bool(data.get("is_private", False)),
            text=str(data.get("text", "") or ""),
            segments=list(data.get("segments", []) or []),
        )


@dataclass
class TurnInfo:
    """轮次信息：本轮随"信息查看"增长的部分。

    ``expansions`` 每项形如::

        {"kind": "reply"|"forward"|"message"|"user", "ref": "456",
         "summary": "上一条说的学费", "content": "完整内容（可选）",
         "source": "expand_message", "bot_id": "...", "at": 1788342260}
    """

    expansions: list[dict] = field(default_factory=list)

    def add(self, kind: str, ref: Any, summary: str = "", *, content: str = "", source: str = "") -> None:
        ref = str(ref or "").strip()
        if not ref or (not summary and not content):
            return
        key = (str(kind), ref)
        for item in self.expansions:
            if (item.get("kind"), str(item.get("ref", ""))) == key:
                item["summary"] = summary or item.get("summary", "")
                if content:
                    item["content"] = content
                if source:
                    item["source"] = source
                return
        self.expansions.append({
            "kind": str(kind),
            "ref": ref,
            "summary": summary,
            "content": content,
            "source": source,
        })

    def by_kind(self, kind: str) -> list[dict]:
        return [e for e in self.expansions if e.get("kind") == kind]

    def find(self, kind: str, ref: Any) -> dict | None:
        ref = str(ref or "")
        for item in self.expansions:
            if item.get("kind") == kind and str(item.get("ref", "")) == ref:
                return item
        return None

    def to_dict(self) -> dict:
        return {"expansions": list(self.expansions)} if self.expansions else {}

    @classmethod
    def from_dict(cls, data: dict | None) -> "TurnInfo":
        data = data or {}
        return cls(expansions=list(data.get("expansions", []) or []))


@dataclass
class HistoryEntry:
    """一条会话历史：基础信息 + 轮次信息（``legacy_text`` 仅用于读旧数据）。"""

    role: str = "user"
    message_id: str = ""
    base: BaseInfo = field(default_factory=BaseInfo)
    turn: TurnInfo = field(default_factory=TurnInfo)
    #: 旧数据：没有结构化字段时的原始 content（渲染时按增强块规则处理）
    legacy_text: str = ""
    #: 会话历史里增强块检测所需的补充行（如"(当前群号: …)"），仅 legacy 路径使用
    meta_lines: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        data: dict[str, Any] = {"role": self.role}
        if self.message_id:
            data["message_id"] = str(self.message_id)
        base = self.base.to_dict()
        if base:
            data["base"] = base
        turn = self.turn.to_dict()
        if turn:
            data["turn"] = turn
        if self.legacy_text:
            data["content"] = self.legacy_text
        return data

    @classmethod
    def from_dict(cls, data: dict | None) -> "HistoryEntry":
        data = data or {}
        raw_base = data.get("base")
        base = BaseInfo.from_dict(raw_base if isinstance(raw_base, dict) else {})
        # 旧形态兼容：会话历史条目把字段平铺在顶层（role/content/time/user_id/nickname）
        if not base.time and data.get("time"):
            base.time = int(data.get("time") or 0)
        if not base.sender_id and data.get("user_id"):
            base.sender_id = str(data.get("user_id") or "")
        if not base.sender_name and data.get("nickname"):
            base.sender_name = str(data.get("nickname") or "")
        text = str(data.get("content", "") or "")
        # 旧数据把「正文」冻在 content 里；结构化后 base.text 才是纯正文
        if not base.text and not base.segments and not isinstance(raw_base, dict) and not text:
            base.text = ""
        return cls(
            role=str(data.get("role", "user") or "user"),
            message_id=str(data.get("message_id", "") or ""),
            base=base,
            turn=TurnInfo.from_dict(data.get("turn") if isinstance(data.get("turn"), dict) else {}),
            legacy_text=text if not isinstance(raw_base, dict) else "",
        )

    @property
    def is_structured(self) -> bool:
        """是否已有结构化基础信息（否则按旧文本渲染）。"""
        base = self.base
        return bool(base.text or base.segments or base.sender_id or base.sender_name)


# ==================== 唯一渲染器 ====================


def render_history_entry(
    entry: HistoryEntry | dict,
    *,
    is_private: bool = False,
    self_ids: set[str] | None = None,
    include_time: bool = True,
    include_user_id: bool = True,
    mask_nickname: bool = False,
    render_content=None,
    normalize_enhanced: bool = False,
) -> str | None:
    """把一条历史渲染成模型可读文本。返回 None 表示"本条不进上下文"。

    Args:
        entry: ``HistoryEntry`` 或旧式 dict（``{role, content, time, user_id, nickname}``）。
        is_private: 私聊形态（对方显示为「对方」）。
        self_ids: bot 自己的 QQ 集合（命中时标为「我」）。
        include_time: 是否加 ``MM-DD HH:MM `` 前缀。
        include_user_id: 群聊是否在昵称后附 ``(QQ)``。
        mask_nickname: 是否把句子型昵称脱敏为 ``用户<QQ>``。
        render_content: ``callable(entry) -> str | None``；给定且条目是结构化的，
            用它的返回值作为正文（用于接入"按需展开/图片展开"等富渲染）。
            返回 None 表示本条无正文（不进上下文）。
        normalize_enhanced: 增强块是否归一化成单行 ``昵称(QQ): 正文``。
    """
    entry = _as_entry(entry)
    role = entry.role or "user"
    # assistant 永远不加前缀：避免模型模仿 "MM-DD HH:MM 我: " 写进回复污染历史
    if role == "assistant":
        content = entry.base.text if entry.is_structured else entry.legacy_text
        return normalize_newlines(content) or None

    self_ids = {str(x) for x in (self_ids or set())}
    base = entry.base

    # 旧数据优先：有 legacy_text 说明"正文已冻在文本里"（可能自带增强分节），
    # 此时 base 里的 time/sender 只是同一条旧记录的回填，不能据此走结构化渲染
    if entry.legacy_text.strip():
        text = normalize_newlines(entry.legacy_text)
        if is_enhanced_content(text):
            if not normalize_enhanced and mask_nickname:
                text = mask_enhanced_content(text)
            return _render_enhanced(text, meta_lines=entry.meta_lines, normalize_enhanced=normalize_enhanced)
        return _single_line(
            content=text,
            time=base.time,
            sender_id=base.sender_id,
            sender_name=base.sender_name,
            is_private=is_private,
            self_ids=self_ids,
            include_time=include_time,
            include_user_id=include_user_id,
            mask_nickname=mask_nickname,
        )

    if entry.is_structured:
        if base.text and is_enhanced_content(base.text):
            return _render_enhanced(base.text, normalize_enhanced=normalize_enhanced)
        if render_content is not None:
            content = render_content(entry)
            if not content or not str(content).strip():
                return None
            content = str(content)
        else:
            content = base.text
            if not str(content or "").strip():
                return None  # 无正文（也没有富渲染钩子）→ 本条不进上下文
        return _single_line(
            content=content,
            time=base.time,
            sender_id=base.sender_id,
            sender_name=base.sender_name,
            is_private=is_private,
            self_ids=self_ids,
            include_time=include_time,
            include_user_id=include_user_id,
            mask_nickname=mask_nickname,
        )

    return None


def _as_entry(entry: HistoryEntry | dict) -> HistoryEntry:
    if isinstance(entry, HistoryEntry):
        return entry
    if not isinstance(entry, dict):
        return HistoryEntry()
    return HistoryEntry.from_dict(entry)


def _single_line(
    *,
    content: str,
    time: Any,
    sender_id: Any,
    sender_name: str,
    is_private: bool,
    self_ids: set[str],
    include_time: bool,
    include_user_id: bool,
    mask_nickname: bool,
) -> str:
    """``MM-DD HH:MM 昵称(QQ): 正文`` 形态。"""
    if sender_id and str(sender_id) in self_ids:
        label = SELF_TAG
    elif is_private:
        label = PRIVATE_OTHER_TAG
    else:
        label = sender_label(
            sender_name, sender_id,
            include_user_id=include_user_id, mask_nickname=mask_nickname,
        )
    prefix = time_prefix(time) if include_time else ""
    return f"{prefix}{label}: {content}"


def _render_enhanced(
    content: str, meta_lines: list[str] | None = None, *, normalize_enhanced: bool = False
) -> str:
    """增强块渲染：默认原样保留；``normalize_enhanced`` 时归一化为单行。"""
    text = normalize_newlines(content)
    if meta_lines:
        extra = [normalize_newlines(line).strip() for line in meta_lines if str(line or "").strip()]
        if extra:
            text = "\n".join([text, *extra])
    if not normalize_enhanced:
        return text
    return normalize_enhanced_content(text)


def normalize_enhanced_content(content: str) -> str:
    """把多行增强块归一化：``发送者：X`` + ``发送了：Y`` → ``X: Y``（时间/群号行保留）。"""
    time_line = ""
    sender_line = ""
    text_line = ""
    meta_lines: list[str] = []
    for line in normalize_newlines(content).split("\n"):
        if line.startswith("(时间：") and line.endswith(")"):
            time_line = line
        elif line.startswith("发送者：") or line.startswith("发送者昵称："):
            sender_line = line.split("：", 1)[1] if "：" in line else ""
        elif line.startswith("发送了：") or line.startswith("消息正文："):
            text_line = line.split("：", 1)[1] if "：" in line else ""
        elif line.strip():
            meta_lines.append(line)

    out: list[str] = []
    if time_line:
        out.append(time_line)
    if sender_line or text_line:
        label = safe_sender_label(sender_line) if sender_line else "用户"
        out.append(f"{label}: {text_line}" if text_line else label)
    out.extend(meta_lines)
    return "\n".join(out) if out else normalize_newlines(content)


def mask_enhanced_content(content: str) -> str:
    """对增强块做"仅脱敏"（保留分节格式），用于不切换新版格式时防昵称泄漏。"""
    out: list[str] = []
    for line in normalize_newlines(content).split("\n"):
        stripped = line.strip()
        if not stripped:
            out.append(line)
            continue
        if stripped.startswith("发送者：") or stripped.startswith("发送者昵称："):
            head, _, tail = stripped.partition("：")
            out.append(f"{head}：{safe_sender_label(tail)}")
        else:
            out.append(line)
    return "\n".join(out)


def render_history(entries: list, *, entry_renderer=None, **kwargs) -> list[dict]:
    """批量渲染为 OpenAI messages；``entry_renderer`` 可为单条渲染器（默认本模块）。"""
    renderer = entry_renderer or render_history_entry
    result: list[dict] = []
    for entry in entries or []:
        content = renderer(entry, **kwargs)
        if content is None:
            continue
        parsed = _as_entry(entry)
        result.append({"role": parsed.role or "user", "content": content})
    return result
