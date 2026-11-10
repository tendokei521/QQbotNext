"""入站去重 + 被动回复窗口（官方协议的两个硬约束）。

1. **重复推送**：官方明确同一 ``msg_id`` 可能重复推送，开发者要按 ``msg_id`` 去重；
2. **被动回复窗口**：回复必须带 ``msg_id`` + ``msg_seq``；群 5 分钟 / 5 次，
   单聊 60 分钟 / 4 次。窗口外/次数用尽只能发主动消息（有配额与用户开关）。

本模块只做记账，不发请求；连接层在发送前问 ``next_passive()``。
"""

from __future__ import annotations

import time
from typing import Any

#: scene → (窗口秒数, 同一 msg_id 最多回复次数)
WINDOWS: dict[str, tuple[float, int]] = {
    "group": (300.0, 5),
    "private": (3600.0, 4),
}

DEFAULT_DEDUPE_TTL = 1800.0
DEFAULT_MAX_SEEN = 4096


class Inbox:
    """一个账号一个实例（内存态，重启后丢失：官方允许重复推送，代价只是多回一次）。"""

    def __init__(
        self,
        *,
        dedupe_ttl: float = DEFAULT_DEDUPE_TTL,
        max_seen: int = DEFAULT_MAX_SEEN,
        clock: Any = time.time,
    ) -> None:
        self._dedupe_ttl = float(dedupe_ttl)
        self._max_seen = int(max_seen)
        self._clock = clock
        self._seen: dict[str, float] = {}
        # (scene, target) → {msg_id, ts, seq, max_seq, window}
        self._windows: dict[tuple[str, str], dict] = {}

    # ---------- 去重 ----------
    def is_duplicate(self, key: str) -> bool:
        """重复事件返回 True；首次出现会记账。"""
        key = str(key or "").strip()
        if not key:
            return False
        now = self._clock()
        self._prune(now)
        if key in self._seen:
            return True
        self._seen[key] = now
        if len(self._seen) > self._max_seen:
            # 简单 FIFO 淘汰：按写入时间排序，丢掉最旧的一批
            for old_key, _ in sorted(self._seen.items(), key=lambda kv: kv[1])[: max(1, self._max_seen // 4)]:
                self._seen.pop(old_key, None)
        return False

    def _prune(self, now: float) -> None:
        if not self._seen:
            return
        cutoff = now - self._dedupe_ttl
        for key in [k for k, ts in self._seen.items() if ts < cutoff]:
            self._seen.pop(key, None)

    # ---------- 被动回复窗口 ----------
    def remember_inbound(self, scene: str, target: Any, msg_id: Any) -> None:
        scene = str(scene or "").strip()
        target = str(target or "").strip()
        msg_id = str(msg_id or "").strip()
        if not scene or not target or not msg_id:
            return
        window, max_seq = WINDOWS.get(scene, WINDOWS["group"])
        self._windows[(scene, target)] = {
            "msg_id": msg_id,
            "ts": self._clock(),
            "seq": 0,
            "max_seq": max_seq,
            "window": window,
        }

    def next_passive(self, scene: str, target: Any) -> tuple[str, int] | None:
        """窗口内返回 ``(msg_id, msg_seq)``；否则 None（只能主动消息或失败）。"""
        key = (str(scene or ""), str(target or "").strip())
        entry = self._windows.get(key)
        if not entry:
            return None
        now = self._clock()
        if now - float(entry["ts"]) > float(entry["window"]):
            self._windows.pop(key, None)
            return None
        if int(entry["seq"]) >= int(entry["max_seq"]):
            return None
        entry["seq"] = int(entry["seq"]) + 1
        return str(entry["msg_id"]), int(entry["seq"])

    def forget(self, scene: str, target: Any) -> None:
        self._windows.pop((str(scene or ""), str(target or "").strip()), None)

    def reset(self) -> None:
        self._seen.clear()
        self._windows.clear()
