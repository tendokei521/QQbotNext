"""取回被 spill 落盘的工具结果（P5 第 3 步）。

## 为什么需要它

spill 把超大结果存进 `data/llm/<bot>/spill/<session>/<sha16>.txt`，消息里只留预览与
路径。没有取回工具的话，"存起来"等于只换了个地方丢——模型看得到路径却读不到内容。

## 边界

- **只读本账号 spill 目录内的文件**：路径先 `resolve()` 再校验是否在允许目录下，
  越界直接拒绝（`PATH_DENIED`）。这是唯一一个新引入的"按路径读文件"入口，必须收严。
- 分页读取（`offset` / `limit`），返回带"[第 a-b / 共 n 字符]"的片段与是否还有更多。
- 空文件 / 不存在都返回 `error:` 文本（走既有错误结果约定），不抛异常。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.agent.tools.spec import ToolSpec

#: 单次最多返回多少字符（模型侧友好；超出就靠 offset 翻页）
DEFAULT_LIMIT = 4000
MAX_LIMIT = 20000


def _allowed_root(bot_id: Any) -> Path:
    from app.infrastructure.fs.paths import bot_data_dir

    return (Path(bot_data_dir(bot_id)) / "spill").resolve()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def read_spill(bot_id: Any, raw_path: str, *, offset: int = 0, limit: int = DEFAULT_LIMIT) -> str:
    """读回落盘结果的一段；任何不合格的输入都返回 ``error:`` 文本。"""
    text_path = str(raw_path or "").strip()
    if not text_path:
        return "error: 缺少 path 参数（spill 落盘路径）"
    try:
        path = Path(text_path).resolve()
    except Exception:  # noqa: BLE001 —— 非法路径
        return f"error: 路径不合法: {text_path}"
    root = _allowed_root(bot_id)
    if not _inside(path, root):
        return "error: PATH_DENIED 只允许读取本账号 spill 目录下的文件"
    if not path.is_file():
        return f"error: 文件不存在: {text_path}"
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as e:
        return f"error: 读取失败: {e}"

    total = len(content)
    try:
        start = max(0, int(offset or 0))
    except (TypeError, ValueError):
        start = 0
    try:
        size = int(limit or DEFAULT_LIMIT)
    except (TypeError, ValueError):
        size = DEFAULT_LIMIT
    size = max(1, min(size, MAX_LIMIT))
    if start >= total:
        return f"error: offset={start} 超出范围（共 {total} 字符）"

    end = min(total, start + size)
    chunk = content[start:end]
    tail = "" if end >= total else f"；还有 {total - end} 字符，用 offset={end} 继续读"
    return f"[第 {start}-{end} / 共 {total} 字符{tail}]\n{chunk}"


def build_recall_tool(runtime: Any, ctx: Any = None) -> ToolSpec:
    """构造 ``recall_tool_result``（系统工具，按需注入）。"""
    bot_id = getattr(runtime, "bot_id", None)

    async def _handler(_ctx, args: dict) -> str:
        payload = args or {}
        return read_spill(
            bot_id,
            str(payload.get("path") or ""),
            offset=payload.get("offset", 0) or 0,
            limit=payload.get("limit", DEFAULT_LIMIT) or DEFAULT_LIMIT,
        )

    return ToolSpec(
        name="recall_tool_result",
        description=(
            "读取被省略的工具结果全文。工具返回里出现「已落盘：<路径>」时，用本工具按该路径"
            "取回内容；结果很长时用 offset 翻页（每次最多 "
            f"{DEFAULT_LIMIT} 字符）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "工具结果里给出的落盘路径（必须在本账号 spill 目录下）",
                },
                "offset": {"type": "integer", "description": "从第几个字符开始读，默认 0"},
                "limit": {
                    "type": "integer",
                    "description": f"本次最多读多少字符，默认 {DEFAULT_LIMIT}，上限 {MAX_LIMIT}",
                },
            },
            "required": ["path"],
        },
        handler=_handler,
        source="system",
        category="系统工具",
        scopes=("*",),
    )
