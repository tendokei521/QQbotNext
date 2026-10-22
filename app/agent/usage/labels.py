"""会话展示名：`名字前5字…` + `group`/`private` + id。

格式（用户指定）：

```
才羽 ミド…group466052056      ← 群名超过 5 字
小明…private1901691195        ← 昵称超过 5 字
测试群group466052056          ← 不超过 5 字，不省略
```

写入时存**快照**，群改名后历史数据不跟着变。
"""

from __future__ import annotations

from typing import Any

#: 名字最多显示几个字
NAME_MAX_CHARS = 5


def short_name(name: Any) -> str:
    """截到 5 个字；超长补一个省略号。"""
    text = str(name or "").strip()
    if not text:
        return ""
    return text[:NAME_MAX_CHARS] + ("…" if len(text) > NAME_MAX_CHARS else "")


def session_label(name: Any, kind: str, ident: Any) -> str:
    """拼展示名：`{短名}{group|private}{id}`。"""
    return f"{short_name(name)}{kind}{ident}"


def label_from_session_id(session_id: Any, name: Any = "") -> str:
    """从 `group_123` / `private_456` 还原展示名（名字未知时只给 kind+id）。"""
    sid = str(session_id or "")
    if sid.startswith("group_"):
        return session_label(name, "group", sid[len("group_") :])
    if sid.startswith("private_"):
        return session_label(name, "private", sid[len("private_") :])
    return sid


__all__ = ["NAME_MAX_CHARS", "label_from_session_id", "session_label", "short_name"]
