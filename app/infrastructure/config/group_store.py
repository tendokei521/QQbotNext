"""群列表缓存（``known_groups``）的存储实现（`ConfigService` mixin）。

## 为什么要有它

OneBot 的 ``get_group_list`` 现在只在连接成功时拉一次，结果只活在
``BotConnection.all_group_list_info`` 内存里（`app/infrastructure/onebot/gateway.py:430-434`）：

- 账号一断开，群列表立刻为空（`_reset_conn_state` 会清掉），WebUI 上"哪个群能聊天"
  就没得选；
- 没有"拉取"按钮可点，也没有历史记录，换设备/重启后用户不知道上次配过哪些群。

这里把群列表落库：在线刷新 → upsert；离线读取 → 上次结果 + ``updated_at`` 判断新鲜度；
机器人退出的群标记 ``in_group=0`` 而**不删除**（保留历史设置，避免"退群再进就丢失开关"）。

数据来源只做"缓存"一件事，权限/开关语义不在这里（见
``docs/permission-dialogue-policy-design.md`` §3 的 ``channel_overrides``）。
"""

from __future__ import annotations

import time
from typing import Any

#: 缓存被认为"新鲜"的时长（秒）；超过就提示用户刷新
STALE_AFTER_S = 300


class GroupStoreMixin:
    """``known_groups`` 的读写。宿主需提供 ``db / log / _known_groups``。"""

    #: 宿主提供的全量配置写串行锁（按任务可重入）
    _write_lock: Any

    # ==================== 读 ====================

    def list_known_groups(self, account_id: object | None = None) -> list[dict]:
        """返回缓存群列表（按"还在群里 → 最近更新 → 群名"排序）。

        ``account_id`` 为 ``None`` 时返回全部账号的（跨账号视图用）。
        """
        target = None if account_id in (None, "") else str(account_id)
        rows = [
            dict(row)
            for (aid, _gid), row in self._known_groups.items()
            if target is None or aid == target
        ]
        rows.sort(key=lambda r: (0 if r.get("in_group") else 1, -int(r.get("updated_at") or 0), r.get("name") or ""))
        return rows

    def known_group_freshness(self, account_id: object | None = None) -> dict:
        """缓存新鲜度摘要：``{count, fetched_at, stale}``。"""
        rows = self.list_known_groups(account_id)
        fetched_at = max((int(r.get("updated_at") or 0) for r in rows), default=0)
        fresh = bool(rows) and (time.time() - fetched_at) <= STALE_AFTER_S
        return {"count": len([r for r in rows if r.get("in_group")]), "fetched_at": fetched_at, "stale": not fresh}

    # ==================== 写 ====================

    async def upsert_known_groups(self, account_id: object, groups: list[dict]) -> int:
        """用一次 ``get_group_list`` 的结果刷新某账号的缓存，返回本次出现的群数。

        规则：
        - 出现的群 → 插入/更新（群名、成员数、``in_group=1``）；
        - 未出现的已知群 → 只把 ``in_group`` 置 0，**不删除行**；
        - 单个群号非法 → 跳过（对端数据脏不能拖垮整次刷新）。
        """
        async with self._write_lock:
            return await self._upsert_known_groups_unlocked(account_id, groups)

    async def _upsert_known_groups_unlocked(self, account_id: object, groups: list[dict]) -> int:
        """`upsert_known_groups` 的实现体（调用方必须已持有写锁）。

        先拼 SQL 并落库，**提交成功后才换内存**：否则事务失败时内存已经变成
        "新群列表"，与库不一致（下次重启又变回去）。
        """
        aid = str(account_id)
        now = int(time.time())
        sqls: list[tuple[str, tuple]] = []
        seen: set[str] = set()
        fresh_rows: dict[tuple[str, str], dict] = {}

        for group in groups or []:
            if not isinstance(group, dict):
                continue
            gid = str(group.get("group_id") or "").strip()
            if not gid:
                continue
            name = str(group.get("group_name") or "").strip() or gid
            member_count = int(group.get("member_count") or 0)
            max_member_count = int(group.get("max_member_count") or 0)
            seen.add(gid)
            fresh_rows[(aid, gid)] = {
                "account_id": aid,
                "group_id": gid,
                "name": name,
                "member_count": member_count,
                "max_member_count": max_member_count,
                "in_group": True,
                "updated_at": now,
            }
            sqls.append((
                "INSERT OR REPLACE INTO known_groups "
                "(account_id, group_id, name, member_count, max_member_count, in_group, updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (aid, gid, name, member_count, max_member_count, 1, now),
            ))

        left_groups: list[tuple[str, str]] = []
        for (row_account, gid), row in list(self._known_groups.items()):
            if row_account != aid or gid in seen or not row.get("in_group"):
                continue
            left_groups.append((row_account, gid))
            sqls.append((
                "UPDATE known_groups SET in_group = 0, updated_at = ? "
                "WHERE account_id = ? AND group_id = ?",
                (now, aid, gid),
            ))

        if sqls:
            await self.db.run_in_transaction(sqls)   # 先落库；失败则内存保持原样

        for key, row in fresh_rows.items():
            self._known_groups[key] = row
        for key in left_groups:
            current = self._known_groups.get(key)
            if current is not None:
                current["in_group"] = False
                current["updated_at"] = now
        return len(seen)

    # ==================== 加载 ====================

    async def _load_known_groups(self) -> None:
        rows = await self.db.fetchall("SELECT * FROM known_groups")
        self._known_groups = {}
        for row in rows:
            aid = str(row["account_id"])
            gid = str(row["group_id"])
            self._known_groups[(aid, gid)] = {
                "account_id": aid,
                "group_id": gid,
                "name": str(row["name"] or ""),
                "member_count": int(row["member_count"] or 0),
                "max_member_count": int(row["max_member_count"] or 0),
                "in_group": bool(row["in_group"]),
                "updated_at": int(row["updated_at"] or 0),
            }
