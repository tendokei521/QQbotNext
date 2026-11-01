"""合并转发摘要缓存（按账号落盘）。

结构：``data/llm/<bot_id>/forward_cache.json``
- key = 转发 id；value = {summary, nodes, chars, created_at, hits}
- 转发内容不可变 → 缓存不失效，只做 LRU(200) + 7 天过期
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from app.agent import bot_data_dir, logger

FILE_NAME = "forward_cache.json"
MAX_ENTRIES = 200
TTL_SECONDS = 7 * 86400


def _path(bot_id: Any) -> str:
    return os.path.join(bot_data_dir(bot_id), FILE_NAME)


def _load(bot_id: Any) -> dict[str, dict]:
    path = _path(bot_id)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:  # noqa: BLE001 —— 缓存损坏按空处理，不影响取回
        logger.debug(f"[上下文] 读取转发缓存失败（按空处理）: {e}")
        return {}


def _save(bot_id: Any, data: dict[str, dict]) -> None:
    path = _path(bot_id)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[上下文] 写转发缓存失败（已忽略）: {e}")


def get(bot_id: Any, forward_id: str) -> dict | None:
    entry = _load(bot_id).get(str(forward_id))
    if not entry:
        return None
    if time.time() - int(entry.get("created_at", 0)) > TTL_SECONDS:
        return None
    return entry


def put(bot_id: Any, forward_id: str, *, summary: str, nodes: int, chars: int) -> None:
    """写入摘要并按 LRU/上限裁剪。"""
    data = _load(bot_id)
    key = str(forward_id)
    data[key] = {
        "summary": summary,
        "nodes": nodes,
        "chars": chars,
        "created_at": int(time.time()),
        "hits": int((data.get(key) or {}).get("hits", 0)) + 1,
    }
    # LRU：先清过期，再按（hits 升序, created_at 升序）丢最旧的
    now = time.time()
    data = {
        k: v for k, v in data.items()
        if now - int(v.get("created_at", 0)) <= TTL_SECONDS
    }
    while len(data) > MAX_ENTRIES:
        victim = min(data.items(), key=lambda kv: (int(kv[1].get("hits", 0)), int(kv[1].get("created_at", 0))))
        data.pop(victim[0], None)
    _save(bot_id, data)


def touch(bot_id: Any, forward_id: str) -> None:
    """命中计数 +1（LRU 用）。"""
    data = _load(bot_id)
    entry = data.get(str(forward_id))
    if not entry:
        return
    entry["hits"] = int(entry.get("hits", 0)) + 1
    _save(bot_id, data)


__all__ = ["FILE_NAME", "MAX_ENTRIES", "TTL_SECONDS", "get", "put", "touch"]
