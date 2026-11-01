"""上下文解析的「约定段」（L1，order 59）。

只讲怎么读 id、怎么查、查不到怎么办——不解释焦点表/预取策略（那些已删除）。
"""

from __future__ import annotations

from typing import Any

CONVENTION = """### 上下文解析
- 消息里的 `[msg:id=..]`、`[reply_to:id=..]`、`[forward_message:id=..]`、`[image:id=..]` 都是可用 id；
- 用户说“那个 / 刚才 / 上一条 / 你指的哪条”时按顺序判断：①本轮引用目标 ②最近一条提到该实体的消息 ③不确定就用 get_context 查，不要猜；
- `get_context` 的 target：`message:<id>` / `recent:<n>` / `forward:<id>` / `user:<qq>` / `image:<id>`；
- 查不到就直接说查不到，不要凭印象编。"""


def _cfg(req: Any, key: str, default: Any):
    getter = getattr(getattr(req, "config", None), "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except Exception:  # noqa: BLE001
            return default
    return default


def convention_section(req: Any) -> list[dict]:
    if not bool(_cfg(req, "context_enable", True)):
        return []
    return [{"role": "system", "content": CONVENTION}]
