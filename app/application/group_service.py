"""群列表服务：把"账号在哪些群里"变成 WebUI 可查询、可刷新的一件事。

分层：
- 实时来源：``BotConnection.all_group_list_info``（连接成功后由 gateway 拉取）；
- 持久缓存：``known_groups``（`GroupStoreMixin`），离线也能看，带 ``updated_at``。

这里只负责「取列表 / 刷新列表 / 给出新鲜度」，**不判断"哪个群能聊天"**——
那是 ``channel_overrides`` 的职责（docs/permission-dialogue-policy-design.md）。
"""

from __future__ import annotations

import time
from typing import Any

from app.core.logger import logger


class GroupService:
    def __init__(self, bot_service: Any, config_service: Any) -> None:
        self.bot_service = bot_service
        self.config_service = config_service

    # ---------- 内部 ----------

    def _gateway(self):
        return getattr(self.bot_service, "gateway", None)

    def _conn(self, account_id: object):
        gateway = self._gateway()
        if gateway is None:
            return None
        try:
            return gateway.connections.get(str(account_id))
        except Exception as e:  # noqa: BLE001 —— 网关未就绪时按"没有连接"处理
            logger.add_info("Groups").debug(f"[Groups] 读连接表失败 {account_id}: {e}")
            return None

    @staticmethod
    def _normalize_live(groups: list[dict]) -> list[dict]:
        out: list[dict] = []
        for group in groups or []:
            if not isinstance(group, dict):
                continue
            gid = str(group.get("group_id") or "").strip()
            if not gid:
                continue
            out.append({
                "account_id": "",
                "group_id": gid,
                "name": str(group.get("group_name") or "").strip() or gid,
                "member_count": int(group.get("member_count") or 0),
                "max_member_count": int(group.get("max_member_count") or 0),
                "in_group": True,
                "updated_at": int(time.time()),
            })
        return out

    # ---------- 查询 ----------

    def list_groups(self, account_id: object) -> dict:
        """返回某账号的群列表视图。

        - 有缓存 → 用缓存（离线可用），并给 ``stale``（超过 5 分钟未刷新）；
        - 没缓存但账号在线 → 回退到内存里的实时列表（``source="live"``，不写库）；
        - 都没有 → 空列表 + ``connected=False``，前端提示"先连接/刷新"。
        """
        aid = str(account_id)
        conn = self._conn(aid)
        connected = bool(conn is not None and getattr(conn, "status", "") == "connected")
        cached = self.config_service.list_known_groups(aid)
        freshness = self.config_service.known_group_freshness(aid)
        source = "cache"
        groups = cached
        if not groups and conn is not None:
            live = self._normalize_live(getattr(conn, "all_group_list_info", []) or [])
            if live:
                groups = live
                source = "live"
        return {
            "account_id": aid,
            "connected": connected,
            "source": source,
            "stale": bool(freshness["stale"]) if source == "cache" else True,
            "fetched_at": freshness["fetched_at"],
            "groups": groups,
        }

    # ---------- 刷新 ----------

    async def refresh(self, account_id: object) -> dict:
        """向 OneBot 拉一次群列表并写入缓存。

        失败一律抛 ``ValueError``（调用方转 400/409），消息可直接展示给用户：
        "账号未连接""对端不支持 get_group_list" 这类原因，比 500 有用得多。
        """
        aid = str(account_id)
        conn = self._conn(aid)
        if conn is None:
            raise ValueError(f"账号不存在或尚未装配连接: {aid}")
        if getattr(conn, "status", "") != "connected":
            raise ValueError("账号未连接，先连接成功后再拉取群列表")

        try:
            resp = await conn.get_group_list()
        except Exception as e:  # noqa: BLE001 —— 网络/超时统一成可读错误
            raise ValueError(f"拉取群列表失败: {e}") from e

        if not isinstance(resp, dict):
            raise ValueError("拉取群列表失败: 对端返回格式不可识别")
        if resp.get("status") != "ok":
            reason = resp.get("message") or resp.get("wording") or resp.get("retcode")
            raise ValueError(f"对端未返回群列表: {reason or '未知原因'}")

        raw_groups = resp.get("data") or []
        if not isinstance(raw_groups, list):
            raise ValueError("拉取群列表失败: data 不是数组")

        # 同步内存视图：UI 立即能看到最新群名/人数
        conn.all_group_list_info = raw_groups
        conn.all_group_list = [
            g.get("group_id", 0) for g in raw_groups
            if isinstance(g, dict) and g.get("group_id")
        ]
        count = await self.config_service.upsert_known_groups(aid, raw_groups)
        return {
            "account_id": aid,
            "ok": True,
            "count": count,
            "groups": self.config_service.list_known_groups(aid),
            "fetched_at": int(time.time()),
        }
