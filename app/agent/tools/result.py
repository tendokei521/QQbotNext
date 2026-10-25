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

    - ``blocks``：内容块（今天只有 text；将来可以是图片/引用等）；
    - ``is_error``：**事实**，不是前缀猜出来的；
    - ``code``：稳定错误码（成功时为空串）；
    - ``meta``：给观测方的补充信息（如落盘路径、原始长度）。
    """

    blocks: tuple[dict, ...] = ()
    is_error: bool = False
    code: str = ""
    meta: dict = field(default_factory=dict)

    # ── 构造 ──────────────────────────────────────────

    @classmethod
    def text(cls, text: str, *, meta: dict | None = None) -> "ToolResult":
        """成功结果。文本本身带 ``error:`` 前缀时**也算错误**（双读过渡）。"""
        content = str(text if text is not None else "")
        if is_error_result(content):
            return cls(
                blocks=({"type": "text", "text": content},),
                is_error=True,
                code="",
                meta=dict(meta or {}),
            )
        return cls(blocks=({"type": "text", "text": content},), meta=dict(meta or {}))

    @classmethod
    def error(
        cls, code: str, message: str = "", *, meta: dict | None = None
    ) -> "ToolResult":
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
        )

    # ── 渲染 ──────────────────────────────────────────

    def plain(self) -> str:
        """拼成字符串（喂给 ``role:tool`` 消息、钩子、遥测）。"""
        parts = [
            str(block.get("text", ""))
            for block in self.blocks
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "".join(parts)

    @property
    def ok(self) -> bool:
        return not self.is_error


def coerce(result: object) -> ToolResult:
    """把任意处理器返回值收敛成 `ToolResult`（处理器可以继续返回 str）。"""
    if isinstance(result, ToolResult):
        return result
    return ToolResult.text(str(result))
