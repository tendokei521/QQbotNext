"""记忆管理器（P1）：随 AgentRuntime 生命周期持有 MemoryStore，提供注入块。

后续阶段扩展：
- P2 挂工具 + 确定性兜底；
- P3 提及扩展 + 审计开关；
- P4 蒸馏 worker（consolidate / stop 内取消）。
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import OrderedDict
from typing import Any

from app.llm import logger
from app.llm.memory import extract as extract_mod
from app.llm.memory.detect import autosave_clause
from app.llm.memory.recall import rank, render_block
from app.llm.memory.store import (
    MemoryStore,
    owner_group,
    owner_group_member,
    owner_private,
)

# 群成员缓存 TTL（秒）：避免每次注入都拉群成员列表
_MEMBER_CACHE_TTL = 300
# @qq 解析
_AT_RE = re.compile(r"\[CQ:at,qq=(\d+)\]|@(\d{5,})")


def scope_owners(session_id: str, user_id: Any = None) -> list[str]:
    """某会话+发言人「可见」的记忆 owner 列表（隔离边界：绝不越权）。"""
    session_id = str(session_id or "")
    if session_id.startswith("private_"):
        return [owner_private(session_id[len("private_"):])]
    if session_id.startswith("group_"):
        gid = session_id[len("group_"):]
        owners = [owner_group(gid)]
        if user_id is not None:
            owners.append(owner_group_member(gid, user_id))
        return owners
    return ["global"]


def _own_owner(session_id: str, user_id: Any = None) -> str:
    """「本人」记忆层 owner：私聊=用户；群聊=群里该成员画像。"""
    session_id = str(session_id or "")
    if session_id.startswith("private_"):
        return owner_private(session_id[len("private_"):])
    if session_id.startswith("group_"):
        return owner_group_member(session_id[len("group_"):], user_id)
    return "global"


class MemoryManager:
    """长期记忆管理器：存储 + 注入（+ 后续工具/蒸馏）。"""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self.bot_id = getattr(runtime, "bot_id", "?")
        self.store = MemoryStore(self.bot_id)
        self._member_cache: dict[str, tuple[float, list[dict]]] = {}
        self._last_distill: dict[str, float] = {}
        self._distill_tasks: set[asyncio.Task] = set()

    # ── 配置 ─────────────────────────────────────────────
    def _get(self, key: str, default: Any = None) -> Any:
        cfg = getattr(self.runtime, "config", None)
        if cfg is None:
            return default
        try:
            return cfg.get(key, default)
        except Exception:
            return default

    def enabled(self) -> bool:
        return bool(self._get("memory_enable", True))

    def scene_enabled(self, session_id: str) -> bool:
        if not self.enabled():
            return False
        if str(session_id or "").startswith("group_"):
            return bool(self._get("memory_group_enable", True))
        return bool(self._get("memory_private_enable", True))

    def scope_owners(self, session_id: str, user_id: Any = None) -> list[str]:
        return scope_owners(session_id, user_id)

    def own_owner(self, session_id: str, user_id: Any = None) -> str:
        """「本人」记忆层（私聊=用户；群聊=群里该成员画像）。"""
        return _own_owner(session_id, user_id)

    def _audit_enabled(self) -> bool:
        return bool(self._get("memory_audit_enable", True))

    def save_fact(
        self,
        content: str,
        owner: str,
        *,
        importance: float = 0.5,
        source: str = "tool",
        source_user: Any = "",
        source_task: str = "",
        keywords: str = "",
    ) -> str | None:
        """写一条记忆（含淘汰 + 审计）。未启用返回 None。"""
        try:
            if not self.enabled():
                return None
            mid = self.store.upsert_fact(
                content, owner,
                importance=importance, keywords=keywords,
                source=source, source_user=str(source_user or ""),
                source_task=str(source_task or ""),
            )
            maxp = int(self._get("memory_max_per_owner", 300))
            self.store.enforce_limit(owner, maxp)
            if self._audit_enabled():
                self.store.audit(
                    "write", owner=owner, user_id=str(source_user or ""),
                    summary=content, source=source, source_task=source_task,
                )
            return mid
        except Exception as e:
            logger.add_info(f"#{self.bot_id}").warning(f"[记忆] 保存失败: {e}")
            return None

    def autosave(self, session_id: str, user_id: Any, text: str) -> str | None:
        """确定性兜底：检测到“记住…”指令直接入库。返回 owner 或 None。"""
        try:
            if not self.scene_enabled(session_id):
                return None
            if not self._get("memory_save_deterministic", True):
                return None
            clause = autosave_clause(text)
            if not clause:
                return None
            owner = self.own_owner(session_id, user_id)
            self.save_fact(
                clause, owner,
                importance=0.8, source="deterministic",
                source_user=str(user_id or ""),
            )
            logger.add_info(f"#{self.bot_id}").info(
                f"[记忆] 确定性兜底写入 {owner}: {clause[:40]}"
            )
            return owner
        except Exception as e:
            logger.add_info(f"#{self.bot_id}").warning(f"[记忆] 自动保存异常: {e}")
            return None

    def visible_recall(
        self,
        session_id: str,
        user_id: Any,
        query: str = "",
        *,
        limit: int | None = None,
        max_chars: int | None = None,
        mention_owners: list[str] | None = None,
        audit: bool = False,
    ) -> str:
        """对「可见 owner」召回并渲染成文本块（供工具与注入复用）。

        audit=True 时记录 read 事件（工具调用场景）。
        """
        if not self.scene_enabled(session_id):
            return ""
        limit = int(limit if limit is not None else self._get("memory_recall_max", 8))
        max_chars = int(max_chars if max_chars is not None else self._get("memory_recall_max_chars", 600))
        owners = self.scope_owners(session_id, user_id)
        hits = rank(
            self.store,
            owners=owners,
            query=query,
            limit=limit,
            max_chars=max_chars,
            mention_owners=mention_owners,
        )
        if audit and self._audit_enabled():
            self.store.audit(
                "read", owner=",".join(owners), user_id=str(user_id or ""),
                summary=f"query={query}", source="tool",
            )
        return render_block(hits)

    def delete_own(self, session_id: str, user_id: Any, target: str) -> int:
        """只删本人层记忆：优先 id，其次词匹配。返回删除条数。"""
        own = self.own_owner(session_id, user_id)
        row = self.store.get_owned(target, own)
        if row:
            self.store.delete_fact(target, owner=own)
            if self._audit_enabled():
                self.store.audit(
                    "forget", owner=own, user_id=str(user_id or ""),
                    summary=row.get("content", ""), source="tool",
                )
            return 1
        deleted = self.store.delete_by_query(own, target)
        if deleted and self._audit_enabled():
            self.store.audit(
                "forget", owner=own, user_id=str(user_id or ""),
                summary=f"query={target}", source="tool",
            )
        return deleted

    # ── 注入 ─────────────────────────────────────────────
    def recall_block(
        self,
        session_id: str,
        user_id: Any = "",
        query: str = "",
        *,
        limit: int | None = None,
        max_chars: int | None = None,
        mention_owners: list[str] | None = None,
        audit_inject: bool | None = None,
    ) -> str:
        """返回可注入 system 的记忆文本块；未启用/无命中返回空串。

        audit_inject=None 时按配置 memory_audit_inject 决定是否记录注入事件。
        """
        try:
            if not self.scene_enabled(session_id):
                return ""
            limit = int(limit if limit is not None else self._get("memory_recall_max", 8))
            max_chars = int(max_chars if max_chars is not None else self._get("memory_recall_max_chars", 600))
            owners = self.scope_owners(session_id, user_id)
            hits = rank(
                self.store,
                owners=owners,
                query=query,
                limit=limit,
                max_chars=max_chars,
                mention_owners=mention_owners,
                require_match=False,  # 注入是“常驻记忆”，不排除未命中项
            )
            block = render_block(hits)
            want_inject = (
                bool(audit_inject) if audit_inject is not None
                else bool(self._get("memory_audit_inject", False))
            )
            if block and want_inject and self._audit_enabled():
                self.store.audit(
                    "inject", owner=",".join(owners), user_id=str(user_id or ""),
                    summary=block[:200], source="inject",
                )
            return block
        except Exception as e:
            logger.add_info(f"#{self.bot_id}").warning(f"[记忆] 注入召回异常: {e}")
            return ""

    # ── 提及扩展（群聊“问某某”定向召回） ───────────────
    async def _group_members(self, bot: Any, group_id: str) -> list[dict]:
        """拉取群成员列表（带 TTL 缓存）；失败返回空列表。"""
        if bot is None:
            return []
        if not group_id:
            return []
        key = f"{self.bot_id}:{group_id}"
        now = time.time()
        cached = self._member_cache.get(key)
        if cached and now - cached[0] < _MEMBER_CACHE_TTL:
            return cached[1]
        try:
            res = await bot.get_group_member_list(group_id=int(group_id))
            if hasattr(res, "data"):
                res = res.data
            if isinstance(res, dict):
                res = res.get("data", None) or res.get("list", None)
            members = res if isinstance(res, list) else []
            parsed = []
            for m in members:
                if not isinstance(m, dict):
                    continue
                uid = m.get("user_id") or m.get("uin") or m.get("qq")
                card = m.get("card") or ""
                nickname = m.get("nickname") or ""
                if uid:
                    parsed.append({"user_id": str(uid), "card": str(card), "nickname": str(nickname)})
            self._member_cache[key] = (now, parsed)
            return parsed
        except Exception as e:
            logger.add_info(f"#{self.bot_id}").warning(f"[记忆] 拉取群成员失败: {e}")
            return []

    async def mention_owners_for(
        self, session_id: str, query: str, bot: Any = None
    ) -> list[str]:
        """解析 query 中 @/昵称指向的群成员 → 其群内画像 owner。

        只用于群聊；解析失败返回空列表（不影响主流程）。
        """
        session_id = str(session_id or "")
        if not session_id.startswith("group_"):
            return []
        gid = session_id[len("group_"):]
        owners: list[str] = []
        for m in _AT_RE.findall(str(query or "")):
            uid = m[0] or m[1]
            if uid:
                owners.append(owner_group_member(gid, uid))
        # 昵称匹配（最长的成员名命中即认为被提及）
        members = await self._group_members(bot, gid)
        text = str(query or "")
        scored = []
        for mem in members:
            name = mem.get("card") or mem.get("nickname") or ""
            name = name.strip()
            if len(name) >= 2 and name in text:
                scored.append((len(name), mem["user_id"]))
        for _ln, uid in sorted(set(scored), reverse=True):
            owners.append(owner_group_member(gid, uid))
        return list(dict.fromkeys(owners))

    async def recall_block_async(
        self,
        session_id: str,
        user_id: Any = "",
        query: str = "",
        *,
        bot: Any = None,
        limit: int | None = None,
        max_chars: int | None = None,
        mention_owners: list[str] | None = None,
    ) -> str:
        """异步注入：先做提及扩展，再交给 recall_block。"""
        mentions = mention_owners
        if mentions is None and bot is not None and str(session_id or "").startswith("group_"):
            mentions = await self.mention_owners_for(session_id, query, bot)
        return self.recall_block(
            session_id, user_id, query,
            limit=limit, max_chars=max_chars,
            mention_owners=mentions,
        )

    async def visible_recall_async(
        self,
        session_id: str,
        user_id: Any,
        query: str = "",
        *,
        bot: Any = None,
        limit: int | None = None,
        max_chars: int | None = None,
        audit: bool = True,
    ) -> str:
        """异步工具召回：提及扩展 + visible_recall。"""
        mentions = None
        if bot is not None and str(session_id or "").startswith("group_"):
            mentions = await self.mention_owners_for(session_id, query, bot)
        return self.visible_recall(
            session_id, user_id, query,
            limit=limit, max_chars=max_chars,
            mention_owners=mentions, audit=audit,
        )

    # ── 隐式蒸馏（P4） ───────────────────────────────────
    def maybe_consolidate(
        self,
        session_id: str,
        is_group: bool,
        messages: list[dict],
        *,
        source: str = "chat",
        force: bool = False,
    ) -> None:
        """触发一次（限频）蒸馏。同步调度后台任务，立即返回。

        messages: [{role, content, user_id}]；只取 role=user 且有 user_id 的。
        """
        try:
            if not self.scene_enabled(session_id):
                return
            if not self._get("memory_extract_enable", True):
                return
            interval = float(self._get("memory_extract_interval_min", 10)) * 60.0
            now = time.time()
            last = self._last_distill.get(session_id, 0.0)
            if not force and now - last < interval:
                return
            self._last_distill[session_id] = now
            task = asyncio.create_task(
                self._run_distill(session_id, is_group, list(messages), source),
                name=f"memory_distill:{session_id}",
            )
            self._distill_tasks.add(task)
            task.add_done_callback(self._distill_tasks.discard)
        except Exception:
            return

    async def _run_distill(
        self, session_id: str, is_group: bool, messages: list[dict], source: str
    ) -> None:
        """后台蒸馏：按用户分组，逐用户提取 → 归属 owner 入库。"""
        try:
            by_user: dict[str, list[str]] = OrderedDict()
            for m in messages or []:
                if (m.get("role") or "user") != "user":
                    continue
                uid = m.get("user_id")
                content = str(m.get("content") or "").strip()
                if not uid or not content:
                    continue
                by_user.setdefault(str(uid), []).append(content)

            total = 0
            # 限制每轮蒸馏的用户数与每个用户的条数，控制成本
            for uid, texts in list(by_user.items())[:3]:
                facts = await extract_mod.extract_facts_for_user_async(
                    self.runtime, uid, texts[-5:], is_group=is_group,
                )
                for f in facts:
                    importance = float(f.get("importance") or 0.6)
                    if importance < 0.6:
                        continue
                    owner = self.own_owner(session_id, uid)
                    if not owner:
                        continue
                    mid = self.save_fact(
                        f["content"], owner,
                        importance=importance,
                        source="extract", source_user=uid,
                    )
                    if mid:
                        total += 1
            if total:
                logger.add_info(f"#{self.bot_id}").info(
                    f"[记忆] 蒸馏沉淀 {total} 条 -> {session_id} ({source})"
                )
        except Exception as e:
            logger.add_info(f"#{self.bot_id}").warning(f"[记忆] 蒸馏失败（忽略）: {e}")

    async def consolidate_archived(
        self,
        session_id: str,
        is_group: bool,
        messages: list[dict],
        *,
        source: str = "archive",
    ) -> None:
        """会话过期归档时对整段对话做最终蒸馏（force 跳过限频）。"""
        self.maybe_consolidate(session_id, is_group, messages, source=source, force=True)

    # ── 生命周期 ─────────────────────────────────────────
    def stop(self) -> None:
        for task in list(self._distill_tasks):
            if not task.done():
                task.cancel()
        self._distill_tasks.clear()
        try:
            self.store.close()
        except Exception:
            pass
