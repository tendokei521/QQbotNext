"""超大工具结果的**落盘 spill**（P5 第 2 步）。

## 为什么要有

工具结果现在按字节截断（`_truncate_result`）：一段长日志被砍掉后**不可恢复**，
模型只能看到开头，用户也拿不回原文。spill 把"砍掉"换成"存起来，消息里给预览 + 取回方式"。

## 做法（借用仓库既有骨架，不另造轮子）

- 目录：`data/llm/<bot_id>/spill/<session_id>/<sha16>.txt`（`bot_data_dir` 已带
  `QQBOT_LLM_DATA_DIR` 测试隔离与路径穿越白名单）；
- 文件名是**内容寻址**的 16 位 sha（同内容天然去重，照抄 `media/store.sha_of`）；
- 写入是**原子**的：`tmp + os.replace`，权限 0600；
- 替换后的正文 = head/tail 预览 + 落盘路径 + "用 recall_tool_result 取回"。

## 必须守住的边界

- **best-effort**：落盘失败只 warn，**保留原文**——绝不能因为存储问题把一次成功调用
  变成失败；
- 跳过取回类工具自身（否则读回来的长文又被存一遍，无限套娃）与 `source="speech"`
  （发言通道的结果是已发给用户的气泡，不该被替换）；
- 开关与阈值都来自配置（`tool_spill_enable` / `tool_spill_max_bytes`），默认开、50KB。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

#: 预览预算：head 占 60%，tail 占 30%，剩下留给说明文字
_HEAD_RATIO = 0.6
_TAIL_RATIO = 0.3

#: 不该被 spill 的工具（取回类自己）
SKIP_TOOLS = frozenset({"recall_tool_result"})


def sha_of_text(text: str) -> str:
    """内容寻址的短片（与图片同一套做法：同内容必然同名）。"""
    return hashlib.sha256(str(text).encode("utf-8", "replace")).hexdigest()[:16]


def spill_dir(bot_id: Any, session_id: str) -> Path:
    from app.infrastructure.fs.paths import bot_data_dir

    root = Path(bot_data_dir(bot_id)) / "spill"
    safe_session = "".join(ch for ch in str(session_id or "unknown") if ch.isalnum() or ch in "_-")
    return root / (safe_session or "unknown")


def spill_bounds(config: Any, default_bytes: int = 50000) -> tuple[bool, int]:
    """读开关与阈值：``(enabled, max_bytes)``；配置读不出来就回默认。"""
    getter = getattr(config, "get", None)
    if not callable(getter):
        return True, default_bytes
    try:
        enabled = bool(getter("tool_spill_enable", True))
    except Exception:  # noqa: BLE001
        enabled = True
    try:
        limit = int(getter("tool_spill_max_bytes", default_bytes) or default_bytes)
    except (TypeError, ValueError):
        limit = default_bytes
    return enabled, max(64, limit)


def write_spill(bot_id: Any, session_id: str, text: str) -> str:
    """把全文落盘，返回路径（失败抛异常，由调用方降级）。"""
    target_dir = spill_dir(bot_id, session_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{sha_of_text(text)}.txt"
    if path.is_file():
        return str(path)     # 同内容已存在：幂等
    tmp = path.with_suffix(".txt.tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)
    return str(path)


def preview(text: str, *, budget: int, path: str = "", retrieval: str = "") -> str:
    """把超长文本压成"head + … + tail + 说明"的预览。"""
    note = retrieval or "可用 recall_tool_result 按路径取回全文"
    note_text = f"\n\n[完整结果共 {len(text)} 字符，已落盘：{path}；{note}]"
    available = max(200, int(budget) - len(note_text))
    head_len = int(available * _HEAD_RATIO)
    tail_len = int(available * _TAIL_RATIO)
    head = text[:head_len]
    tail = text[-tail_len:] if tail_len else ""
    return f"{head}\n…\n{tail}{note_text}"


def apply_spill(
    result: str,
    *,
    runtime: Any,
    session_id: str,
    tool_name: str = "",
    source: str = "system",
    preview_budget: int = 4000,
) -> tuple[str, str]:
    """必要时把结果换成"预览 + 落盘路径"，返回 ``(正文, 路径)``。

    任何一步失败都返回原文（``path`` 为空）：spill 是增强，不是必需品。
    """
    text = str(result or "")
    if not text:
        return text, ""
    if tool_name in SKIP_TOOLS or source == "speech":
        return text, ""
    config = getattr(runtime, "config", None) if runtime is not None else None
    enabled, limit = spill_bounds(config)
    if not enabled or len(text) <= limit:
        return text, ""
    bot_id = getattr(runtime, "bot_id", None) if runtime is not None else None
    try:
        path = write_spill(bot_id, session_id, text)
    except Exception as e:  # noqa: BLE001 —— 落盘失败：保留原文，只留痕
        from app.core.logger import logger

        logger.warning(f"[Spill] 落盘失败，保留原文（{tool_name}）: {e}")
        return text, ""
    return preview(text, budget=max(200, int(preview_budget or 4000)), path=path), path
