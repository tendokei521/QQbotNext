"""可插拔的压缩后端（Q1）。

## 三种后端

| 后端 | 调不调 LLM | 干什么 |
|---|---|---|
| `summary` | 调 | 把旧段交给摘要模型，生成一段摘要（今天的行为） |
| `prune` | **不调** | 只把旧段里"过长且较早"的工具结果替换成占位（可用 `recall_tool_result` 取回） |
| `both` | 调 | 先 prune 再 summary：先砍掉占大头的工具输出，摘要只处理对话文本 |

为什么默认是 `summary` 而不是 `both`：保持升级前后行为一致（用户不会突然发现
"摘要里的工具信息不见了"）。想省钱/省延迟的部署把 `context_compress_backend`
改成 `prune` 或 `both` 即可。

## 边界

- 后端**只处理"旧段"**（调用方用 `pairing.legal_cut` 切好）；保留段原样不动；
- 绝不切开工具配对（切点由调用方保证，本模块只做替换/摘要）；
- 失败一律交回原始历史（与今天一致）：压缩是优化，不是需求。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

#: 被剪掉的工具结果的占位文案（要求能被模型理解成"可以取回"）
PRUNED_TOOL_MARKER = "[已省略 {chars} 字符的工具结果；需要时可用 recall_tool_result 按路径取回]"


@dataclass(frozen=True)
class CompactionPlan:
    """一次压缩的输入事实（调用方已经算好，后端只读）。"""

    trigger_ratio: float = 0.0
    keep_ratio: float = 0.0
    keep_tail: int = 0
    reason: str = ""            # "pressure" | "count"


@dataclass(frozen=True)
class CompactionResult:
    """一次压缩的产出：新历史 + 是否真的改了 + 摘要文本（便于记事件）。"""

    history: list[dict]
    compacted: bool = False
    summary: str = ""
    pruned: int = 0
    replaced: int = 0


class CompactionBackend(Protocol):
    name: str

    async def compact(
        self,
        old: list[dict],
        keep: list[dict],
        *,
        plan: CompactionPlan,
        provider_chain: list[dict] | None = None,
        config: Any = None,
    ) -> CompactionResult: ...


def prune_old_tool_results(old: list[dict], *, min_chars: int = 2000) -> tuple[list[dict], int]:
    """把旧段里过长的工具结果换成占位，返回 ``(新列表, 剪掉的条数)``。

    只动 `role == "tool"` 且内容超阈值的条目：对话文本一个字不改（摘要后端会处理）。
    """
    out: list[dict] = []
    pruned = 0
    for message in old or []:
        if not isinstance(message, dict) or str(message.get("role") or "") != "tool":
            out.append(message)
            continue
        content = message.get("content")
        text = content if isinstance(content, str) else ""
        if len(text) <= max(0, int(min_chars)):
            out.append(message)
            continue
        out.append({**message, "content": PRUNED_TOOL_MARKER.format(chars=len(text))})
        pruned += 1
    return out, pruned


class SummaryBackend:
    """今天的行为：把旧段交给摘要模型（失败回退原始历史）。"""

    name = "summary"

    async def compact(self, old, keep, *, plan, provider_chain=None, config=None):
        from app.agent.assembly.compress import compress_history

        summary = await compress_history(provider_chain or [], config or {}, old)
        if not summary:
            return CompactionResult(history=list(old) + list(keep))
        head = [{"role": "system", "content": "【更早对话摘要】\n" + summary}]
        return CompactionResult(
            history=head + list(keep), compacted=True, summary=summary, replaced=len(old)
        )


class PruneBackend:
    """零 LLM 成本：只剪旧工具结果，对话文本原样保留。"""

    name = "prune"

    async def compact(self, old, keep, *, plan, provider_chain=None, config=None):
        pruned, count = prune_old_tool_results(old)
        if not count:
            return CompactionResult(history=list(old) + list(keep))
        return CompactionResult(
            history=pruned + list(keep), compacted=True, pruned=count, replaced=len(old)
        )


class BothBackend:
    """先剪工具结果，再把剩下的旧段交给摘要模型。"""

    name = "both"

    async def compact(self, old, keep, *, plan, provider_chain=None, config=None):
        pruned, count = prune_old_tool_results(old)
        from app.agent.assembly.compress import compress_history

        summary = await compress_history(provider_chain or [], config or {}, pruned)
        if not summary:
            # 摘要失败：至少把工具结果剪了（这一半是确定收益）
            return CompactionResult(
                history=pruned + list(keep), compacted=bool(count), pruned=count,
                replaced=len(old),
            )
        head = [{"role": "system", "content": "【更早对话摘要】\n" + summary}]
        return CompactionResult(
            history=head + list(keep), compacted=True, summary=summary,
            pruned=count, replaced=len(old),
        )


_BACKENDS: dict[str, type] = {
    "summary": SummaryBackend,
    "prune": PruneBackend,
    "both": BothBackend,
}


def choose_backend(config: Any) -> CompactionBackend:
    """按 `context_compress_backend` 选后端；不认识的值回退 `summary`。"""
    getter = getattr(config, "get", None)
    name = "summary"
    if callable(getter):
        try:
            name = str(getter("context_compress_backend", "summary") or "summary").strip().lower()
        except Exception:  # noqa: BLE001 —— 读不出来就用默认
            name = "summary"
    return _BACKENDS.get(name, SummaryBackend)()
