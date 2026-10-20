"""本轮请求 messages 的落盘通道（迁移对账 / 与第三方框架逐条 diff）。

日志里的 ``[Prompt]`` 是给人看的，拿来做 diff 不可靠（截断、格式、多行）。这里把
``post_assemble`` 之后的**最终 messages** 以 JSONL 原样落盘：

    data/llm/<bot_id>/prompt_dump.jsonl

设计约束：
- **默认关闭**（``prompt_dump_enable=False``）：正常运行时零 IO；
- 每个 bot 一个滚动文件，只保留最后 ``prompt_dump_max_entries`` 条；
- 任何异常都只 debug 留痕、返回 False，**绝不阻断本轮请求**。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from app.agent import bot_data_dir, logger
from app.core import redact

FILE_NAME = "prompt_dump.jsonl"
DEFAULT_MAX_ENTRIES = 20


def dump_path(bot_id: Any) -> str:
    """该账号的 dump 文件路径（data/llm/<bot_id>/prompt_dump.jsonl）。"""
    return os.path.join(bot_data_dir(bot_id), FILE_NAME)


def _cfg(config: Any, key: str, default: Any) -> Any:
    getter = getattr(config, "get", None)
    if not callable(getter):
        return default
    try:
        return getter(key, default)
    except Exception:  # noqa: BLE001 —— 配置对象异常不该影响落盘判定
        return default


def enabled(config: Any) -> bool:
    return bool(_cfg(config, "prompt_dump_enable", False))


def max_entries(config: Any) -> int:
    """滚动保留条数：非法值回落默认，负数夹到 1，0/空值视为"用默认"。"""
    try:
        return max(1, int(_cfg(config, "prompt_dump_max_entries", DEFAULT_MAX_ENTRIES) or DEFAULT_MAX_ENTRIES))
    except (TypeError, ValueError):
        return DEFAULT_MAX_ENTRIES


def _read_all(path: str) -> list[dict]:
    if not os.path.isfile(path):
        return []
    entries: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue  # 半行（上次写崩）直接跳过，不让一条坏行毁掉整个文件
            if isinstance(item, dict):
                entries.append(item)
    return entries


def _write_all(path: str, entries: list[dict]) -> None:
    """整文件重写（先写临时文件再替换，避免中途崩溃留下半行）。"""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def _short_media_url(value: Any) -> str:
    """把 base64 图片地址压成占位符（与日志出口同一套规则，见 ``app/core/redact.py``）。"""
    text = str(value or "")
    if not text.startswith("data:"):
        return text
    return str(redact.shorten_data_url(text))


def _redact_media(value: Any) -> Any:
    """递归把多模态块里的 base64 载荷脱敏。

    图片改成"本地字节内联"之后，一条 user 消息可能捎带几 MB 的 base64；dump 通道是
    JSONL 且保留最近若干条，原样写进去会让文件迅速涨到上百 MB（还拖慢每次写入）。
    只留"这里有一张 data:image/png 的图、载荷多长"，对账时够用。
    """
    if isinstance(value, list):
        return [_redact_media(item) for item in value]
    if isinstance(value, dict):
        return {
            key: (
                {**val, "url": _short_media_url(val.get("url"))}
                if key == "image_url" and isinstance(val, dict)
                else _redact_media(val)
            )
            for key, val in value.items()
        }
    if isinstance(value, str) and value.startswith("data:image/"):
        return _short_media_url(value)
    return value


def record(
    runtime: Any,
    messages: list[dict],
    *,
    bot_id: Any = None,
    session_id: str = "",
    kind: str = "",
) -> bool:
    """把本轮最终 messages 追加进滚动 JSONL（base64 图片载荷先脱敏）。

    返回是否真的落盘；未开启、序列化失败、磁盘异常等一律返回 False 并 debug 留痕，
    绝不向上抛 —— 这条通道是排查辅助，不能反过来影响对话。
    """
    try:
        config = getattr(runtime, "config", None)
        if not enabled(config):
            return False
        bot = bot_id if bot_id is not None else getattr(runtime, "bot_id", None)
        path = dump_path(bot)
        entry = {
            "ts": int(time.time()),
            "bot_id": bot,
            "session_id": str(session_id or ""),
            "kind": str(kind or "chat"),
            "count": len(messages or []),
            "messages": _redact_media(list(messages or [])),
        }
        entries = _read_all(path)
        entries.append(entry)
        entries = entries[-max_entries(config):]
        _write_all(path, entries)
        return True
    except Exception as e:  # noqa: BLE001 —— 落盘是辅助能力，失败不得影响请求
        logger.add_info(f"#{getattr(runtime, 'bot_id', '?')}").debug(f"[PromptDump] 落盘失败（已忽略）: {e}")
        return False


def load_recent(
    bot_id: Any,
    *,
    session_id: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """读取最近的 dump 条目（旧 → 新）；``session_id`` 非空时只看该会话。"""
    try:
        entries = _read_all(dump_path(bot_id))
    except Exception as e:  # noqa: BLE001 —— 读取失败按"没有记录"处理
        logger.add_info(f"#{bot_id}").debug(f"[PromptDump] 读取失败（按空处理）: {e}")
        return []
    if session_id:
        entries = [e for e in entries if str(e.get("session_id", "")) == str(session_id)]
    if limit and limit > 0:
        entries = entries[-int(limit):]
    return entries


def clear(bot_id: Any) -> bool:
    """删除该账号的 dump 文件；返回是否真的删了。"""
    path = dump_path(bot_id)
    try:
        if os.path.isfile(path):
            os.remove(path)
            return True
    except OSError as e:
        logger.add_info(f"#{bot_id}").debug(f"[PromptDump] 删除失败（已忽略）: {e}")
    return False
