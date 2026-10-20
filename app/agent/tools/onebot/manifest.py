"""OneBot API 工具清单（数据在 ``manifest.json``，本文件只负责加载与派生）。

## 为什么清单是 JSON 而不是 Python 字面量

这里原本是 4,792 行的 `ONEBOT_TOOLS: list[dict] = [...]` 字面量——它是**数据**，
不是代码，却占了整个 `app/llm/onebot_tools/` 目录的绝大部分篇幅，让「安全策略在哪」
「执行逻辑在哪」都看不见。改成 `manifest.json` 之后：

- 加一条工具 = 改一小段 JSON（不用在 4,792 行的 Python diff 里定位）；
- 结构可被测试校验（`tests/test_onebot_manifest_data.py` 检查必填字段、
  枚举值、重名、`doc_url` 形态）；
- 目录里只剩两个 .py：本文件（加载）与 `tools.py`（执行）——职责一眼可见。

## 每条记录的字段

- ``name``: 对应 OneBot action
- ``description``: 给 LLM 看的说明
- ``parameters``: OpenAI function calling 参数 schema
- ``risk``: read / send / admin
- ``permission``: 默认工具权限（member / group_admin / group_owner / owner）
- ``scopes``: group / private / ``["*"]``
- ``category`` / ``doc_url`` / ``action``（可选）

## ``sensitivity`` 为什么不写进 JSON

它是**派生字段**：由本文件里的 `_CRITICAL_TOOLS` / `_HIGH_TOOLS` 两份策略名单决定。
把「哪些操作算高危」留在代码里（一处可审计、diff 一目了然），数据文件只放目录。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MANIFEST_PATH = Path(__file__).with_name("manifest.json")

# ==================== 敏感度标注（写死，不随前端编辑改变）====================
_CRITICAL_TOOLS = {
    "set_group_leave",
    "set_group_whole_ban",
    "set_group_kick",
    "set_group_admin",
    "set_group_name",
    "clean_cache",
}
_HIGH_TOOLS = {
    "delete_msg",
    "set_essence_msg",
    "delete_essence_msg",
    "set_group_card",
    "set_group_ban",
    "set_group_add_request",
    "set_friend_add_request",
}


def _load_catalog() -> list[dict[str, Any]]:
    """读 JSON 清单，并为每条记录补上派生的 ``sensitivity``。"""
    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise TypeError(f"{MANIFEST_PATH.name} 顶层必须是数组，实际是 {type(raw).__name__}")
    catalog: list[dict[str, Any]] = []
    for entry in raw:
        name = str(entry.get("name", ""))
        if name in _CRITICAL_TOOLS:
            entry["sensitivity"] = "critical"
        elif name in _HIGH_TOOLS:
            entry["sensitivity"] = "high"
        else:
            entry["sensitivity"] = "normal"
        catalog.append(entry)
    return catalog


ONEBOT_TOOLS: list[dict[str, Any]] = _load_catalog()

__all__ = ["MANIFEST_PATH", "ONEBOT_TOOLS"]
