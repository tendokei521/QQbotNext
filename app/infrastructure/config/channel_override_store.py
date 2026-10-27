"""群级覆盖（``channel_overrides``）的存储实现（`ConfigService` mixin）。

一行 = ``(account_id, capability, channel_id)`` 的覆盖：

- ``enabled``：``True/False`` 覆盖，``None`` 表示"不覆盖，继承"；
- ``config``：**默认层/账号层/群层可覆盖的键子集**（白名单见
  `app/domain/dialogue_policy.py` 的 ``SCOPE_OVERRIDABLE``）；
- ``permission``：``role_min`` / ``user_mode`` / ``user_list``；
- ``muted_until``：临时静音到点自动恢复（读时比较时间，不需要后台任务）。

存储层只做"按行读写"，**不**做白名单校验与合并——那两件事分别属于
``DialoguePolicyService``（校验）与 ``resolve_policy``（纯解析）。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

#: 「本参数未提供」哨兵：区分"不改"与"显式写 None/False"
_UNSET = object()


class ChannelOverrideStoreMixin:
    """``channel_overrides`` 的读写。宿主需提供 ``db / log / _channel_overrides``。"""

    # ==================== 读 ====================

    def get_channel_override(
        self,
        account_id: object,
        capability: str = "agent",
        channel_id: object = "",
    ) -> dict | None:
        key = (str(account_id), str(capability), str(channel_id or ""))
        row = self._channel_overrides.get(key)
        return dict(row) if row is not None else None

    def list_channel_overrides(
        self,
        account_id: object | None = None,
        capability: str | None = None,
    ) -> list[dict]:
        aid = None if account_id in (None, "") else str(account_id)
        cap = None if capability in (None, "") else str(capability)
        rows = [
            dict(row)
            for (row_aid, row_cap, _channel), row in self._channel_overrides.items()
            if (aid is None or row_aid == aid) and (cap is None or row_cap == cap)
        ]
        rows.sort(key=lambda r: (r["account_id"], r["capability"], r["channel_id"]))
        return rows

    # ==================== 写 ====================

    async def save_channel_override(
        self,
        account_id: object,
        capability: str = "agent",
        channel_id: object = "",
        *,
        enabled: Any = _UNSET,
        config: dict | None = None,
        permission: dict | None = None,
        muted_until: Any = _UNSET,
    ) -> dict:
        """写入一行覆盖（缺省参数 = 保持原值）。

        ``config`` / ``permission`` 传入即**整值替换**（调用方负责合并出最终值）；
        传 ``None`` 表示"本次不改"。要清空某层，用 ``delete_channel_override``。
        """
        key = (str(account_id), str(capability), str(channel_id or ""))
        row = dict(self._channel_overrides.get(key) or {
            "account_id": key[0],
            "capability": key[1],
            "channel_id": key[2],
            "enabled": None,
            "config": {},
            "permission": {},
            "muted_until": 0,
            "updated_at": 0,
        })
        if enabled is not _UNSET:
            row["enabled"] = None if enabled is None else bool(enabled)
        if config is not None:
            row["config"] = dict(config or {})
        if permission is not None:
            row["permission"] = dict(permission or {})
        if muted_until is not _UNSET:
            row["muted_until"] = int(muted_until or 0)
        row["updated_at"] = int(time.time())

        # 全空行没有任何意义（等于"没有覆盖"）→ 直接删掉，保持表干净
        if row["enabled"] is None and not row["config"] and not row["permission"] and not row["muted_until"]:
            await self.delete_channel_override(key[0], key[1], key[2])
            return row

        self._channel_overrides[key] = row
        await self._persist_channel_override(key)
        return dict(row)

    async def delete_channel_override(
        self,
        account_id: object,
        capability: str = "agent",
        channel_id: object = "",
    ) -> bool:
        key = (str(account_id), str(capability), str(channel_id or ""))
        existed = self._channel_overrides.pop(key, None) is not None
        if existed:
            await self.db.execute(
                "DELETE FROM channel_overrides WHERE account_id = ? AND capability = ? AND channel_id = ?",
                key,
            )
        return existed

    async def _persist_channel_override(self, key: tuple[str, str, str]) -> None:
        try:
            row = self._channel_overrides.get(key)
            if row is None:
                return
            enabled = row.get("enabled")
            await self.db.execute(
                "INSERT OR REPLACE INTO channel_overrides "
                "(account_id, capability, channel_id, enabled, config_json, permission_json, muted_until, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    key[0], key[1], key[2],
                    None if enabled is None else (1 if enabled else 0),
                    self.db.dumps(row.get("config") or {}),
                    self.db.dumps(row.get("permission") or {}),
                    int(row.get("muted_until") or 0),
                    int(row.get("updated_at") or time.time()),
                ),
            )
        except Exception as e:  # noqa: BLE001 —— 落盘失败要留痕，但不能把写操作炸掉
            self.log.error(f"[Config] 持久化 channel_override 失败 {key}: {e}")

    # ==================== 加载 ====================

    async def _load_channel_overrides(self) -> None:
        rows = await self.db.fetchall("SELECT * FROM channel_overrides")
        self._channel_overrides = {}
        for row in rows:
            aid = str(row["account_id"])
            cap = str(row["capability"] or "agent")
            channel = str(row["channel_id"] or "")
            enabled = row["enabled"]
            self._channel_overrides[(aid, cap, channel)] = {
                "account_id": aid,
                "capability": cap,
                "channel_id": channel,
                "enabled": None if enabled is None else bool(enabled),
                "config": self.db.loads(row["config_json"], {}) or {},
                "permission": self.db.loads(row["permission_json"], {}) or {},
                "muted_until": int(row["muted_until"] or 0),
                "updated_at": int(row["updated_at"] or 0),
            }
