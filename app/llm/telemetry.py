"""LLM 可观测性：统一记录调用、工具、钩子耗时与聚合指标。

设计目标：
- 每次 LLM 请求都有 latency / tokens / provider / model / 是否成功；
- 工具调用与 LLM 钩子阶段有独立耗时；
- WebUI 通过 AgentRuntime.telemetry 读取最近记录和聚合统计。
数据仅保存在内存中（单机运行足够），后续可扩展为持久化/时序存储。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any

# 单机保留最近记录数量
_MAX_RECORDS = 2000


@dataclass
class LLMCallRecord:
    """一次 LLM 请求记录。"""

    timestamp: float = field(default_factory=time.time)
    bot_id: Any = None
    session_id: str = ""
    provider: str = ""
    model: str = ""
    stream: bool = False
    success: bool = True
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    message_count: int = 0
    tool_calls: int = 0
    characters: int = 0
    fallback_count: int = 0
    error: str = ""
    hook_times: dict = field(default_factory=dict)


@dataclass
class ToolCallRecord:
    """一次 LLM 工具调用记录。"""

    timestamp: float = field(default_factory=time.time)
    bot_id: Any = None
    session_id: str = ""
    name: str = ""
    success: bool = True
    duration_ms: float = 0.0
    error: str = ""


class TelemetryRecorder:
    """按 AgentRuntime 持有的轻量级 LLM 指标记录器（线程安全）。"""

    def __init__(self, max_records: int = _MAX_RECORDS) -> None:
        self._records: deque[LLMCallRecord] = deque(maxlen=max_records)
        self._tool_records: deque[ToolCallRecord] = deque(maxlen=max_records)
        self._hook_times: dict[str, float] = {}
        self._hook_counts: dict[str, int] = {}
        self._lock = threading.Lock()

    # ---------- 写入 ----------

    def record_call(self, record: LLMCallRecord) -> None:
        with self._lock:
            self._records.append(record)

    def record_tool(self, record: ToolCallRecord) -> None:
        with self._lock:
            self._tool_records.append(record)

    def record_hook(self, stage: str, duration_ms: float) -> None:
        with self._lock:
            self._hook_times[stage] = self._hook_times.get(stage, 0.0) + duration_ms
            self._hook_counts[stage] = self._hook_counts.get(stage, 0) + 1

    # ---------- 读取 ----------

    def recent(self, limit: int = 30) -> list[dict]:
        with self._lock:
            records = list(self._records)[-limit:]
        return [asdict(r) for r in records]

    def recent_tools(self, limit: int = 30) -> list[dict]:
        with self._lock:
            records = list(self._tool_records)[-limit:]
        return [asdict(r) for r in records]

    def stats(self) -> dict:
        with self._lock:
            records = list(self._records)
            tool_records = list(self._tool_records)

        total = len(records)
        success = sum(1 for r in records if r.success)
        calls = [r for r in records if not r.stream]
        streams = [r for r in records if r.stream]

        def _avg(seq, key):
            if not seq:
                return 0.0
            return round(sum(getattr(r, key) for r in seq) / len(seq), 2)

        per_model: dict[str, dict] = {}
        for r in records:
            bucket = per_model.setdefault(f"{r.provider}/{r.model}", {
                "calls": 0, "success": 0, "error": 0,
                "latency_ms_sum": 0.0, "input_tokens": 0, "output_tokens": 0,
                "tool_calls": 0, "fallback_count": 0,
            })
            bucket["calls"] += 1
            if r.success:
                bucket["success"] += 1
            else:
                bucket["error"] += 1
            bucket["latency_ms_sum"] += r.latency_ms
            bucket["input_tokens"] += r.input_tokens
            bucket["output_tokens"] += r.output_tokens
            bucket["tool_calls"] += r.tool_calls
            bucket["fallback_count"] += r.fallback_count

        for bucket in per_model.values():
            bucket["avg_latency_ms"] = round(
                bucket["latency_ms_sum"] / bucket["calls"], 2
            ) if bucket["calls"] else 0.0
            del bucket["latency_ms_sum"]

        return {
            "total_calls": total,
            "success_calls": success,
            "error_calls": total - success,
            "success_rate": round(success / total, 4) if total else 1.0,
            "avg_latency_ms": round(sum(r.latency_ms for r in records) / total, 2) if total else 0.0,
            "call_latency_ms": _avg(calls, "latency_ms"),
            "stream_latency_ms": _avg(streams, "latency_ms"),
            "total_input_tokens": sum(r.input_tokens for r in records),
            "total_output_tokens": sum(r.output_tokens for r in records),
            "total_tool_calls": sum(r.tool_calls for r in records),
            "total_fallbacks": sum(r.fallback_count for r in records),
            "per_model": per_model,
            "recent_limit": len(self._records),
            "hook_avg_ms": {
                k: round(self._hook_times.get(k, 0.0) / self._hook_counts.get(k, 1), 2)
                for k in self._hook_times
            },
            "tool_total": len(tool_records),
            "tool_success": sum(1 for r in tool_records if r.success),
            "tool_error": sum(1 for r in tool_records if not r.success),
            "tool_avg_ms": round(sum(r.duration_ms for r in tool_records) / len(tool_records), 2) if tool_records else 0.0,
        }

    def reset(self) -> dict:
        with self._lock:
            self._records.clear()
            self._tool_records.clear()
            self._hook_times.clear()
            self._hook_counts.clear()
        return {"ok": True, "message": "telemetry cleared"}

    # ---------- 便捷写入 ----------

    def record_call_simple(
        self,
        *,
        bot_id: Any = None,
        session_id: str = "",
        provider: str = "",
        model: str = "",
        stream: bool = False,
        success: bool = True,
        latency_ms: float = 0.0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        message_count: int = 0,
        tool_calls: int = 0,
        characters: int = 0,
        fallback_count: int = 0,
        error: str = "",
        hook_times: dict | None = None,
    ) -> None:
        self.record_call(LLMCallRecord(
            bot_id=bot_id,
            session_id=session_id,
            provider=provider,
            model=model,
            stream=stream,
            success=success,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            message_count=message_count,
            tool_calls=tool_calls,
            characters=characters,
            fallback_count=fallback_count,
            error=error,
            hook_times=dict(hook_times or {}),
        ))
