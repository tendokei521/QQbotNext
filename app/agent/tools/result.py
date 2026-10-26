"""工具结果的**结构化契约**（P5 第 1 步）。

## 为什么要有

改造前工具结果就是一个 `str`，"成功/失败"靠 `"error:"` 前缀猜（`is_error_result`），
错误码只活在日志行的自由文本里（`timeout(20s)` / `HTTP 404` …）。于是：

- 上层想按"是不是权限问题"分支处理，只能去 match 中文文案；
- 遥测/钩子拿到的 `error` 是给人看的句子，不是可聚合的码；
- 60 个生产者各写各的前缀，永远没法统一。

`ToolResult` 把结果变成"内容块 + 是否错误 + 稳定错误码"，字符串只是它的
**渲染形式**（`plain()`）。改造采用**双读过渡**：`is_error` 既看结构化标记也看老的
`"error:"` 前缀，所以 60 个生产者可以一个都不改就完成升级。

## Q6：表达力（非文本块 + 追加上下文 + 收尾）

`plain()` 此前只认 ``type=text``，其它块被静默丢掉——于是图片只能靠
`round_images` 这个特例走旁路，引用块（"这条引用指向哪条消息"）根本没有表示法。
现在补上两类块：

- ``{"type": "image", "sha": ..., "mime": ..., "alt": ...}`` → ``[图片: alt 或 sha/mime]``
- ``{"type": "ref", "label": ..., "target": ...}`` → ``[引用: label → target]``

另外两个字段把"结果的**副作用**"也变成契约（此前只能靠工具自己拼文案）：

- ``additional_contexts``：工具想额外告诉模型的上下文（由循环体汇总成一条 user 消息）；
- ``concludes_turn``：工具要求结束本轮（`finish` 类语义），循环体据此停止再请求。

两者都带默认值，旧构造（含位置参数）一个字都不用改。

## 错误码（与计划 §1.2 一致）

`UNKNOWN_TOOL` / `INVALID_ARGS` / `TOOL_TIMEOUT` / `TOOL_UNAVAILABLE` /
`PERMISSION_DENIED` / `ABORTED_BEFORE_DISPATCH` / `ABORTED` / `TOOL_EXCEPTION` /
`OUTPUT_LIMIT`
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: 稳定错误码（可聚合、可分支；不再从文案里猜）
UNKNOWN_TOOL = "UNKNOWN_TOOL"
INVALID_ARGS = "INVALID_ARGS"
TOOL_TIMEOUT = "TOOL_TIMEOUT"
TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"
PERMISSION_DENIED = "PERMISSION_DENIED"
ABORTED_BEFORE_DISPATCH = "ABORTED_BEFORE_DISPATCH"
ABORTED = "ABORTED"
TOOL_EXCEPTION = "TOOL_EXCEPTION"
OUTPUT_LIMIT = "OUTPUT_LIMIT"

#: 所有合法错误码（测试用；新增码必须登记）
ERROR_CODES = frozenset({
    UNKNOWN_TOOL,
    INVALID_ARGS,
    TOOL_TIMEOUT,
    TOOL_UNAVAILABLE,
    PERMISSION_DENIED,
    ABORTED_BEFORE_DISPATCH,
    ABORTED,
    TOOL_EXCEPTION,
    OUTPUT_LIMIT,
})


def is_error_result(text: object) -> bool:
    """旧的文本判定（保留一版双读；大版本后删）。

    工具用 ``"error: ..."`` 文本报错是历史约定，很多生产者还在这么做。
    """
    return str(text or "").lstrip().lower().startswith("error:")


@dataclass(frozen=True)
class ToolResult:
    """一次工具执行的结果。

    - ``blocks``：内容块（text / image / ref；未知类型在 ``plain()`` 里被忽略）；
    - ``is_error``：**事实**，不是前缀猜出来的；
    - ``code``：稳定错误码（成功时为空串）；
    - ``meta``：给观测方的补充信息（如落盘路径、原始长度）；
    - ``additional_contexts``：工具追加给模型的额外上下文（循环体汇总成一条 user 消息）；
    - ``concludes_turn``：工具要求结束本轮（``finish`` 类），循环体不再发下一次请求。
    """

    blocks: tuple[dict, ...] = ()
    is_error: bool = False
    code: str = ""
    meta: dict = field(default_factory=dict)
    additional_contexts: tuple[str, ...] = ()
    concludes_turn: bool = False

    # ── 构造 ──────────────────────────────────────────

    @classmethod
    def text(
        cls,
        text: str,
        *,
        meta: dict | None = None,
        additional_contexts: tuple[str, ...] | list[str] = (),
        concludes_turn: bool = False,
    ) -> ToolResult:
        """成功结果。文本本身带 ``error:`` 前缀时**也算错误**（双读过渡）。"""
        content = str(text if text is not None else "")
        extra = _contexts(additional_contexts)
        if is_error_result(content):
            return cls(
                blocks=({"type": "text", "text": content},),
                is_error=True,
                code="",
                meta=dict(meta or {}),
                additional_contexts=extra,
                concludes_turn=bool(concludes_turn),
            )
        return cls(
            blocks=({"type": "text", "text": content},),
            meta=dict(meta or {}),
            additional_contexts=extra,
            concludes_turn=bool(concludes_turn),
        )

    @classmethod
    def error(
        cls,
        code: str,
        message: str = "",
        *,
        meta: dict | None = None,
        additional_contexts: tuple[str, ...] | list[str] = (),
        concludes_turn: bool = False,
    ) -> ToolResult:
        """失败结果。``message`` 是给人看的句子，``code`` 是给机器看的。"""
        body = str(message or "").strip()
        if not body:
            text = f"error: {code or '工具执行失败'}"
        elif body.lower().startswith("error:"):
            # 已经是错误文本（执行器把带前缀的正文原样传进来）：不要再加一次前缀
            text = body
        else:
            text = f"error: {body}"
        return cls(
            blocks=({"type": "text", "text": text},),
            is_error=True,
            code=str(code or ""),
            meta=dict(meta or {}),
            additional_contexts=_contexts(additional_contexts),
            concludes_turn=bool(concludes_turn),
        )

    @classmethod
    def image(
        cls,
        sha: str = "",
        mime: str = "",
        *,
        alt: str = "",
        meta: dict | None = None,
        additional_contexts: tuple[str, ...] | list[str] = (),
        concludes_turn: bool = False,
    ) -> ToolResult:
        """图片结果（本地点指向 + mime；``alt`` 是给人/给模型的简短说明）。

        ``sha`` 不是必填：远程 URL 类图片可以把 URL 放进 ``sha``（渲染时同等对待），
        但内容**不会**被塞进文本——那会把上下文撑爆，图片本体走既有补图通道。
        """
        block = {"type": "image", "sha": str(sha or ""), "mime": str(mime or "")}
        alt_text = str(alt or "").strip()
        if alt_text:
            block["alt"] = alt_text
        return cls(
            blocks=(block,),
            meta=dict(meta or {}),
            additional_contexts=_contexts(additional_contexts),
            concludes_turn=bool(concludes_turn),
        )

    @classmethod
    def ref(
        cls,
        label: str = "",
        target: str = "",
        *,
        meta: dict | None = None,
        additional_contexts: tuple[str, ...] | list[str] = (),
        concludes_turn: bool = False,
    ) -> ToolResult:
        """引用结果（``label`` 是"引用的是什么"，``target`` 是"指向哪里"）。"""
        return cls(
            blocks=({
                "type": "ref",
                "label": str(label or ""),
                "target": str(target or ""),
            },),
            meta=dict(meta or {}),
            additional_contexts=_contexts(additional_contexts),
            concludes_turn=bool(concludes_turn),
        )

    # ── 渲染 ──────────────────────────────────────────

    def plain(self) -> str:
        """拼成字符串（喂给 ``role:tool`` 消息、钩子、遥测）。

        非文本块渲染成**可读的占位标记**而不是丢掉：模型至少要能说出"我看过一张
        图片/一条引用"，否则它只能假装没这回事（图片本体另走 `round_images`）。
        """
        parts: list[str] = []
        for block in self.blocks:
            if not isinstance(block, dict):
                continue
            kind = str(block.get("type") or "")
            if kind == "text":
                parts.append(str(block.get("text", "")))
            elif kind == "image":
                parts.append(_render_image_block(block))
            elif kind == "ref":
                parts.append(_render_ref_block(block))
        return "".join(parts)

    @property
    def ok(self) -> bool:
        return not self.is_error


def _contexts(values: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    """规整 ``additional_contexts``：去掉空白项、统一成 tuple（frozen 之前就要定型）。"""
    if not values:
        return ()
    if isinstance(values, str):
        # 单个字符串是常见的手滑写法：当成"一条上下文"，不要按字符拆开
        values = (values,)
    return tuple(text for text in (str(v).strip() for v in values) if text)


def _render_image_block(block: dict) -> str:
    """``[图片: alt 或 sha/mime]``——alt 优先（人写的一句话比指纹好读）。"""
    alt = str(block.get("alt") or "").strip()
    if alt:
        return f"[图片: {alt}]"
    sha = str(block.get("sha") or block.get("sha256") or "").strip()
    mime = str(block.get("mime") or "").strip()
    ident = "/".join(part for part in (sha, mime) if part) or "无标识"
    return f"[图片: {ident}]"


def _render_ref_block(block: dict) -> str:
    """``[引用: label → target]``。"""
    label = str(block.get("label") or "").strip() or "未命名"
    target = str(block.get("target") or "").strip() or "未指定"
    return f"[引用: {label} → {target}]"


def coerce(result: object) -> ToolResult:
    """把任意处理器返回值收敛成 `ToolResult`（处理器可以继续返回 str）。"""
    if isinstance(result, ToolResult):
        return result
    return ToolResult.text(str(result))
