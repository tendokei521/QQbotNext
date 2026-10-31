"""记忆管理器（v3）：把存储 / 抽取 / 召回 / 生命周期缝在一个对象上。

## 分层

- **热路径**（每轮请求）：只读——画像块 + 混合召回；原始语料写 ``episodes`` 是 O(1)
  追加，不调用模型；embedding 查询有 LRU 缓存。失败一律降级为空，不阻断聊天。
- **后台路径**：``Consolidator`` 攒批调用一次 LLM 抽取 → 记忆操作（ADD/UPDATE/
  SUPERSEDE/NOOP）→ 写库 + 审计；``embedding_worker`` 定时补齐缺失向量。

## 口径

所有策略键走 ``policy_get(runtime, "memory", key)``（模块接管时读模块配置，未接管
回退 agent 配置）。数据库是同步 SQLite，本模块在事件循环里统一用
``asyncio.to_thread`` 访问，避免阻塞聊天。
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from app.agent import logger
from app.core.logger import account_label

from . import detect
from .consolidate import Consolidator
from .embeddings import EmbeddingClient
from .recall import clean_query, rank, render_block, render_profile
from .store import (
    MemoryStore,
    owner_group,
    owner_group_member,
    owner_private,
)

# 群成员缓存 TTL（秒）：避免每次注入都拉群成员列表
_MEMBER_CACHE_TTL = 300
# @qq 解析：[CQ:at,qq=123] 或裸 @12345
_AT_RE = re.compile(r"\[CQ:at,qq=(\d+)\]|@(\d{5,})")

# 各来源的默认置信度
_SOURCE_CONFIDENCE = {
    "tool": 0.80,
    "deterministic": 0.80,
    "correct": 0.85,
    "extract": 0.60,
    "manual": 0.80,
}

# 「用户明确保存/已确认」型：会话重置（suspend）后仍可注入（与 recall 口径一致）
_SAVED_SOURCES = ("tool", "deterministic", "correct", "manual")


def scope_owners(session_id: str, user_id: Any = None) -> list[str]:
    """某会话+发言人「可见」的记忆 owner 列表（隔离边界：绝不越权）。"""
    session_id = str(session_id or "")
    if session_id.startswith("private_"):
        return [owner_private(session_id[len("private_"):])]
    if session_id.startswith("group_"):
        gid = session_id[len("group_"):]
        owners = [owner_group(gid)]
        if user_id is not None and str(user_id or "").strip():
            owners.append(owner_group_member(gid, user_id))
        return owners
    return ["global"]


def own_owner(session_id: str, user_id: Any = None) -> str:
    """「本人」记忆层 owner：私聊=用户；群聊=群里该成员画像。"""
    session_id = str(session_id or "")
    if session_id.startswith("private_"):
        return owner_private(session_id[len("private_"):])
    if session_id.startswith("group_"):
        return owner_group_member(session_id[len("group_"):], user_id)
    return "global"


def _group_id(session_id: str) -> str:
    sid = str(session_id or "")
    return sid[len("group_"):] if sid.startswith("group_") else ""


class MemoryManager:
    """长期记忆管理器：存储 + 召回 + 写入 + 后台蒸馏。"""

    def __init__(self, module: Any) -> None:
        self.module = module
        self.runtime = self._resolve_runtime(module)
        self.bot_id = getattr(module, "bot_id", "?")
        self.store = MemoryStore(self.bot_id)
        self.consolidator = Consolidator(module, self)
        self._embedding: EmbeddingClient | None = None
        self._embedding_model_id: str | None = None
        self._member_cache: dict[str, tuple[float, list[dict]]] = {}
        self._stopped = False
        self._worker_task: asyncio.Task | None = None
        self._running: set[str] = set()

    # ── 基础设施 ─────────────────────────────────────────
    @staticmethod
    def _resolve_runtime(module: Any) -> Any:
        getter = getattr(module, "_runtime", None)
        if callable(getter):
            try:
                return getter()
            except Exception as e:  # noqa: BLE001 —— 取不到运行时按未接管处理
                logger.debug(f"[Memory] 读取运行时失败（已忽略）: {e}")
                return None
        return getattr(module, "runtime", None)

    def _log(self, level: str = "debug"):
        try:
            return getattr(logger.add_info(account_label(self.bot_id)), level)
        except Exception:  # noqa: BLE001 —— 日志器不可用时退回全局 logger，避免记忆主流程被日志拖垮
            return getattr(logger, level)

    def _get(self, key: str, default: Any = None) -> Any:
        runtime = self.runtime
        if runtime is not None:
            try:
                from app.agent.policy import policy_get

                return policy_get(runtime, "memory", key, default)
            except Exception as e:  # noqa: BLE001 —— 配置读取失败不该中断记忆
                self._log("debug")(f"[Memory] 读取配置 {key} 失败（用默认值）: {e}")
        config = getattr(self.module, "config", None)
        getter = getattr(config, "get", None)
        if callable(getter):
            try:
                return getter(key, default)
            except Exception as e:  # noqa: BLE001
                self._log("debug")(f"[Memory] 模块配置读取 {key} 失败（用默认值）: {e}")
        return default

    def _services(self) -> Any:
        return getattr(getattr(self.module, "ctx", None), "services", None)

    # ── 开关与作用域 ─────────────────────────────────────
    def enabled(self) -> bool:
        return bool(self._get("memory_enable", False))

    def scene_enabled(self, session_id: str) -> bool:
        if not self.enabled():
            return False
        session_id = str(session_id or "")
        if session_id.startswith("group_"):
            return bool(self._get("memory_group_enable", True))
        return bool(self._get("memory_private_enable", True))

    def scope_owners(self, session_id: str, user_id: Any = None) -> list[str]:
        return scope_owners(session_id, user_id)

    def own_owner(self, session_id: str, user_id: Any = None) -> str:
        return own_owner(session_id, user_id)

    def recall_mode(self) -> str:
        mode = str(self._get("memory_recall_mode", "hybrid") or "hybrid").strip().lower()
        return mode if mode in ("hybrid", "keyword") else "hybrid"

    # ── embedding ────────────────────────────────────────
    def embedding_client(self) -> EmbeddingClient:
        """按当前配置构造（或复用）Embedding 客户端；配置改了自动重建。"""
        wanted = str(self._get("memory_embedding_model_id", "") or "")
        if self._embedding is None or self._embedding_model_id != wanted:
            self._embedding = EmbeddingClient(
                runtime=self.runtime,
                services=self._services(),
                bot_id=self.bot_id,
                model_id=wanted,
            )
            self._embedding_model_id = wanted
        return self._embedding

    async def embedding_status(self) -> dict:
        client = self.embedding_client()
        info = client.describe()
        try:
            info["stats"] = await asyncio.to_thread(self.store.stats)
        except Exception as e:  # noqa: BLE001 —— 诊断信息取不到不影响主流程
            info["stats"] = {}
            self._log("debug")(f"[Memory] 统计失败: {e}")
        return info

    async def _query_vector(self, text: str) -> list[float] | None:
        """查询向量：只在 hybrid 模式 + 客户端可用时尝试，失败返回 None。"""
        if not text or self.recall_mode() != "hybrid":
            return None
        try:
            client = self.embedding_client()
            if not await client.available():
                return None
            return await client.embed_one(text)
        except Exception as e:  # noqa: BLE001 —— 向量化失败退化为词法召回
            self._log("debug")(f"[Memory] 查询向量化失败（退化为词法召回）: {e}")
            return None

    # ── 画像块 ───────────────────────────────────────────
    async def visible_profiles(self, session_id: str, user_id: Any = None) -> list[dict]:
        """本会话可见 owner 的画像记忆（按重要度排序，去重）。"""
        if not self.enabled() or not bool(self._get("memory_profile_enable", True)):
            return []
        owners = self.scope_owners(session_id, user_id)
        try:
            rows: list[dict] = []
            for owner in owners:
                rows.extend(await asyncio.to_thread(self.store.list_profiles, owner, 20))
        except Exception as e:  # noqa: BLE001 —— 画像读失败按空处理
            self._log("debug")(f"[Memory] 读取画像失败（已忽略）: {e}")
            return []
        seen: set[str] = set()
        unique: list[dict] = []
        for row in rows:
            content = str(row.get("content") or "").strip()
            if not content or content in seen:
                continue
            seen.add(content)
            unique.append(row)
        unique.sort(key=lambda r: (-float(r.get("importance") or 0), -int(r.get("updated_at") or 0)))
        return unique

    async def profile_block(self, session_id: str, user_id: Any = None) -> str:
        """常驻画像块（Letta 式：字符预算 + 自曝用量）。"""
        profiles = await self.visible_profiles(session_id, user_id)
        if not profiles:
            return ""
        budget = int(self._get("memory_profile_max_chars", 800) or 800)
        kept: list[dict] = []
        used = 0
        for row in profiles:
            length = len(str(row.get("content") or "")) + 2
            if kept and used + length > budget:
                break
            kept.append(row)
            used += length
        text = render_profile(kept)
        if not text:
            return ""
        return f"{text}\n（以上画像共 {used}/{budget} 字，可能过时，可让用户纠正）"

    # ── 召回 ─────────────────────────────────────────────
    async def _recall_params(self, owners: list[str]) -> dict:
        resets: dict[str, int] = {}
        try:
            for owner in owners:
                resets[owner] = await asyncio.to_thread(self.store.get_reset, owner)
        except Exception as e:  # noqa: BLE001 —— 重置线读不到按未重置处理
            self._log("debug")(f"[Memory] 读取重置线失败（按未重置处理）: {e}")
        return {
            "min_confidence": float(self._get("memory_min_confidence", 0.5)),
            "max_age_days": float(self._get("memory_max_age_days", 0)),
            "owner_resets": resets,
            "keep_saved_before_reset": bool(self._get("memory_upgrade_saved_only", True)),
            "hedge": bool(self._get("memory_inject_hedge", True)),
        }

    async def recall_block_async(
        self,
        session_id: str,
        user_id: Any = "",
        query: str = "",
        *,
        bot: Any = None,
        limit: int | None = None,
        max_chars: int | None = None,
        for_tool: bool = False,
    ) -> str:
        """召回并渲染事实记忆块；未启用/无命中返回空串。"""
        if not self.scene_enabled(session_id):
            return ""
        try:
            limit = int(limit if limit is not None else self._get("memory_recall_max", 8))
            max_chars = int(
                max_chars if max_chars is not None else self._get("memory_recall_max_chars", 1200)
            )
            # 规则化清洗 query：剥「你还记得吗/之前说过」这类元话语（不改实体词）
            query = clean_query(query)
            owners = self.scope_owners(session_id, user_id)
            mention_owners: list[str] = []
            if str(session_id or "").startswith("group_") and bot is not None:
                mention_owners = await self.mention_owners_for(session_id, query, bot)
            params = await self._recall_params(owners)
            rank_kwargs = {
                "owners": owners,
                "mention_owners": mention_owners,
                "query": query,
                "limit": limit,
                "max_chars": max_chars,
                "require_match": bool(for_tool),
                "min_confidence": 0.0 if for_tool else params["min_confidence"],
                "max_age_days": params["max_age_days"],
                "owner_resets": params["owner_resets"],
                "keep_saved_before_reset": params["keep_saved_before_reset"],
            }
            # 两段式召回（延迟优化）：先跑词法（毫秒级）；只有显式检索或词法结果
            # 为空/偏弱时，才多花一次 embedding 请求做向量精排。
            hits = await asyncio.to_thread(rank, self.store, query_embedding=None, **rank_kwargs)
            weak = (not hits) or float(hits[0].get("_score") or 0.0) < 0.35
            if query and (for_tool or weak):
                query_vector = await self._query_vector(query)
                if query_vector:
                    hits = await asyncio.to_thread(
                        rank, self.store, query_embedding=query_vector, **rank_kwargs
                    )
            if not hits:
                return ""
            block = render_block(
                hits,
                hedge=params["hedge"],
                annotate_age=bool(self._get("memory_inject_recency", True)),
            )
            ids = [str(h.get("id") or "") for h in hits if h.get("id")]
            if ids:
                try:
                    await asyncio.to_thread(self.store.touch_used, ids)
                except Exception as e:  # noqa: BLE001 —— 使用计数失败不影响召回
                    self._log("debug")(f"[Memory] 更新使用次数失败（已忽略）: {e}")
            await self._audit(
                "read" if for_tool else "inject",
                owner=",".join(owners),
                user_id=str(user_id or ""),
                summary=block[:200],
                source="tool" if for_tool else "inject",
            )
            return block
        except Exception as e:  # noqa: BLE001 —— 召回失败不能毁掉本轮
            self._log("warning")(f"[Memory] 召回失败（已忽略）: {e}")
            return ""

    async def search_async(
        self, session_id: str, user_id: Any, query: str, *, limit: int = 8
    ) -> str:
        """工具/命令显式检索：低置信也展示，未命中返回空串。"""
        return await self.recall_block_async(
            session_id,
            user_id,
            query,
            limit=max(1, min(30, int(limit or 8))),
            max_chars=3000,
            for_tool=True,
        )

    async def _audit(self, action: str, **kwargs: Any) -> None:
        if not bool(self._get("memory_audit_enable", True)):
            return
        if action == "inject" and not bool(self._get("memory_audit_inject", False)):
            return
        try:
            await asyncio.to_thread(self.store.audit, action, **kwargs)
        except Exception as e:  # noqa: BLE001 —— 审计失败不影响业务
            self._log("debug")(f"[Memory] 审计写入失败（已忽略）: {e}")

    # ── 提及扩展（群聊“问某某”定向召回） ───────────────
    async def _group_members(self, bot: Any, group_id: str) -> list[dict]:
        if bot is None or not group_id:
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
            parsed = [
                {
                    "user_id": str(m.get("user_id") or m.get("uin") or m.get("qq") or ""),
                    "card": str(m.get("card") or ""),
                    "nickname": str(m.get("nickname") or ""),
                }
                for m in members
                if isinstance(m, dict) and (m.get("user_id") or m.get("uin") or m.get("qq"))
            ]
            self._member_cache[key] = (now, parsed)
            return parsed
        except Exception as e:  # noqa: BLE001 —— 拉群成员失败只影响提及扩展
            self._log("warning")(f"[Memory] 拉取群成员失败: {e}")
            return []

    async def mention_owners_for(self, session_id: str, query: str, bot: Any = None) -> list[str]:
        """解析 query 中 @/昵称指向的群成员 → 其群内画像 owner（失败返回空）。"""
        session_id = str(session_id or "")
        if not session_id.startswith("group_"):
            return []
        gid = session_id[len("group_"):]
        owners: list[str] = []
        for match in _AT_RE.findall(str(query or "")):
            uid = match[0] or match[1]
            if uid:
                owners.append(owner_group_member(gid, uid))
        members = await self._group_members(bot, gid)
        text = str(query or "")
        scored: list[tuple[int, str]] = []
        for member in members:
            name = (member.get("card") or member.get("nickname") or "").strip()
            if len(name) >= 2 and name in text:
                scored.append((len(name), member["user_id"]))
        for _length, uid in sorted(set(scored), reverse=True):
            owners.append(owner_group_member(gid, uid))
        return list(dict.fromkeys(owners))

    # ── 写入 ─────────────────────────────────────────────
    def save_fact(
        self,
        content: str,
        owner: str,
        *,
        importance: float = 0.5,
        source: str = "tool",
        source_user: Any = "",
        source_task: str = "",
        kind: str = "fact",
        confidence: float | None = None,
        keywords: str = "",
        meta: dict | None = None,
        supersede_conflicts: bool | None = None,
    ) -> str | None:
        """写一条记忆（去重/失效/画像/淘汰/审计），未启用返回 None。"""
        try:
            if not self.enabled():
                return None
            # 所有写入都过同一道安全过滤：注入特征直接丢弃，PII 掩码后才落库
            # （工具/命令/兜底路径以前绕过了抽取侧的 sanitize，这里收口）。
            from .extract import sanitize_content

            content = sanitize_content(content)
            if not content:
                self._log("debug")("[Memory] 写入内容被安全过滤丢弃")
                return None
            conf = (
                float(confidence)
                if confidence is not None
                else float(_SOURCE_CONFIDENCE.get(source, 0.6))
            )
            if supersede_conflicts is None:
                supersede_conflicts = source in _SAVED_SOURCES
            outcome = self.store.upsert_fact(
                content,
                owner,
                importance=importance,
                confidence=conf,
                source=source,
                source_user=str(source_user or ""),
                source_task=str(source_task or ""),
                kind=kind,
                keywords=keywords,
                meta=meta or {},
                supersede_conflicts=bool(supersede_conflicts),
            )
            self.store.enforce_limit(owner, int(self._get("memory_max_per_owner", 200)))
            if bool(self._get("memory_audit_enable", True)):
                self.store.audit(
                    "write",
                    owner=owner,
                    ref_id=outcome.id,
                    user_id=str(source_user or ""),
                    summary=content,
                    source=source,
                    source_task=source_task,
                )
            return outcome.id
        except Exception as e:  # noqa: BLE001 —— 单条写入失败不中断对话
            self._log("warning")(f"[Memory] 保存失败: {e}")
            return None

    async def save_fact_async(self, content: str, owner: str, **kwargs: Any) -> str | None:
        return await asyncio.to_thread(self.save_fact, content, owner, **kwargs)

    def autosave(self, session_id: str, user_id: Any, text: str) -> str | None:
        """确定性兜底：检测到「记住…」类指令直接入库；画像型事实写 kind=profile。"""
        try:
            if not self.scene_enabled(session_id):
                return None
            if not bool(self._get("memory_save_deterministic", True)):
                return None
            owner = self.own_owner(session_id, user_id)
            profile = detect.extract_for_profile(text)
            if profile:
                mid = self.save_fact(
                    profile["content"],
                    owner,
                    importance=0.9,
                    source="deterministic",
                    source_user=str(user_id or ""),
                    kind="profile",
                )
                if mid:
                    self._log("info")(f"[Memory] 画像兜底写入 {owner}: {profile['content'][:40]}")
                return owner if mid else None
            clause = detect.autosave_clause(text)
            if not clause:
                return None
            mid = self.save_fact(
                clause,
                owner,
                importance=0.85,
                source="deterministic",
                source_user=str(user_id or ""),
            )
            if mid:
                self._log("info")(f"[Memory] 确定性兜底写入 {owner}: {clause[:40]}")
                return owner
            return None
        except Exception as e:  # noqa: BLE001
            self._log("warning")(f"[Memory] 自动保存异常: {e}")
            return None

    def record_episode(
        self,
        session_id: str,
        user_id: Any,
        text: str,
        *,
        message_id: str = "",
        role: str = "user",
    ) -> None:
        """热路径只追加原始语料（O(1)），供后台蒸馏。"""
        if not self.scene_enabled(session_id):
            return
        content = str(text or "").strip()
        if not content:
            return
        try:
            owner = self.own_owner(session_id, user_id)
            self.store.add_episode(
                session_id,
                owner,
                content[:1000],
                role=role,
                user_id=str(user_id or ""),
                message_id=str(message_id or ""),
            )
        except Exception as e:  # noqa: BLE001 —— 语料缓冲失败不该阻断本轮
            self._log("debug")(f"[Memory] 记录语料失败（已忽略）: {e}")

    # ── 蒸馏 ─────────────────────────────────────────────
    def candidates_for_extraction(
        self, session_id: str, is_group: bool, text: str
    ) -> list[dict]:
        """给抽取器看的已有记忆候选（同 owner 域 + 相关词法命中）。"""
        owners = self.scope_owners(session_id)
        try:
            episodes = self.store.list_episodes(
                session_id, limit=int(self._get("memory_consolidate_batch", 20))
            )
            for ep in episodes:
                owner = str(ep.get("owner") or "")
                if owner and owner not in owners:
                    owners.append(owner)
        except Exception as e:  # noqa: BLE001 —— 候选集构建失败退化为可见 owner
            self._log("debug")(f"[Memory] 读取语料失败（按可见 owner 处理）: {e}")
        try:
            hits = rank(
                self.store,
                owners=owners,
                query=text,
                limit=15,
                max_chars=3000,
                require_match=False,
                min_confidence=0.0,
                max_age_days=0.0,
            )
            return [
                {"id": str(h.get("id") or ""), "content": str(h.get("content") or ""), "kind": h.get("kind") or "fact"}
                for h in hits
            ]
        except Exception as e:  # noqa: BLE001
            self._log("debug")(f"[Memory] 构建抽取候选失败（已忽略）: {e}")
            return []

    async def maybe_consolidate(
        self,
        session_id: str,
        is_group: bool,
        messages: list[dict] | None = None,
        *,
        force: bool = False,
        source: str = "chat",
    ) -> None:
        """记录本轮消息并（限频）调度一次后台蒸馏；立即返回。

        兼容两种入参形状（历史回归）：``history_snapshot()`` 的 ``list[dict]``，
        以及误传的 ``dict``（旧代码传 ``session_mgr.sessions``）。后者按「值里的
        消息」处理，绝不因形状错误把整轮话题打断。
        """
        if isinstance(messages, dict):
            messages = [item for item in messages.values() if isinstance(item, dict)]
        for message in messages or []:
            if not isinstance(message, dict):
                continue
            if (message.get("role") or "user") != "user":
                continue
            content = str(message.get("content") or "").strip()
            if not content:
                continue
            self.record_episode(
                session_id,
                message.get("user_id"),
                content,
                message_id=str(message.get("message_id") or ""),
            )
        if not self.enabled() or not self.scene_enabled(session_id):
            return
        if not bool(self._get("memory_extract_enable", True)):
            return
        if session_id in self._running:
            return
        if not force and not self.consolidator.should_run(session_id):
            return
        self._running.add(session_id)

        async def _run() -> None:
            try:
                await self.consolidator.run(
                    session_id, is_group=is_group, force=force, source=source
                )
            except Exception as e:  # noqa: BLE001 —— 后台失败只留痕，下次再试
                self._log("warning")(f"[Memory] 后台蒸馏失败: {e}")
            finally:
                self._running.discard(session_id)

        spawn = getattr(self.module, "spawn", None)
        try:
            if callable(spawn):
                spawn(_run(), name=f"memory-consolidate:{self.bot_id}:{session_id}")
            else:
                asyncio.create_task(_run(), name=f"memory-consolidate:{session_id}")
        except Exception as e:  # noqa: BLE001 —— 调度失败不能让一轮对话炸掉
            self._running.discard(session_id)
            self._log("warning")(f"[Memory] 蒸馏调度失败: {e}")

    async def consolidate_archived(
        self, session_id: str, is_group: bool, messages: list[dict], **kwargs: Any
    ) -> int:
        """会话归档时对整段历史做一次最终蒸馏（跳过限频）。"""
        try:
            return await self.consolidator.run(
                session_id, is_group=is_group, force=True, source="archive"
            )
        except Exception as e:  # noqa: BLE001
            self._log("warning")(f"[Memory] 归档蒸馏失败（已忽略）: {e}")
            return 0

    async def apply_op(
        self,
        op: dict,
        *,
        session_id: str,
        is_group: bool,
        speaker_uid: str = "",
        speaker_owner: str = "",
    ) -> str | None:
        """执行一条记忆操作（ADD/UPDATE/SUPERSEDE/NOOP），返回新记忆 id。"""
        try:
            kind_op = str(op.get("op") or "ADD").strip().upper()
            if kind_op == "NOOP":
                return None
            content = str(op.get("content") or "").strip()
            if not content:
                return None
            owner = self._resolve_op_owner(op, session_id, is_group, speaker_uid, speaker_owner)
            if not owner:
                return None
            kind = str(op.get("kind") or "fact").strip().lower()
            if kind not in ("fact", "profile", "preference", "event", "instruction"):
                kind = "fact"
            importance = max(0.0, min(1.0, float(op.get("importance") or 0.6)))
            confidence = max(0.0, min(1.0, float(op.get("confidence") or 0.6)))
            target_id = str(op.get("target_id") or "").strip()
            if kind_op in ("UPDATE", "SUPERSEDE") and target_id:
                allowed = set(self.scope_owners(session_id, speaker_uid))
                allowed.add(owner)
                row = await asyncio.to_thread(self.store.get, target_id)
                if row is not None and row.get("owner") in allowed:
                    await asyncio.to_thread(
                        self.store.set_status,
                        target_id,
                        "superseded",
                        owner=row.get("owner"),
                        reason=f"op:{kind_op}",
                    )
                else:
                    self._log("debug")(f"[Memory] 忽略越权 target_id={target_id}（按 ADD 处理）")
            meta = {
                "reason": str(op.get("reason") or "")[:200],
                "source": "extract",
            }
            return await self.save_fact_async(
                content,
                owner,
                importance=importance,
                confidence=confidence,
                source="extract",
                source_user=speaker_uid,
                kind=kind,
                meta=meta,
                supersede_conflicts=True,
            )
        except Exception as e:  # noqa: BLE001 —— 单条 op 失败不拖垮整批
            self._log("warning")(f"[Memory] 执行记忆操作失败（跳过）: {e}")
            return None

    def _resolve_op_owner(
        self,
        op: dict,
        session_id: str,
        is_group: bool,
        speaker_uid: str,
        speaker_owner: str,
    ) -> str:
        gid = _group_id(session_id)
        subject = str(op.get("subject") or "self").strip()
        if subject in ("", "self"):
            return speaker_owner or own_owner(session_id, speaker_uid)
        if subject == "group":
            return owner_group(gid) if (is_group and gid) else (speaker_owner or own_owner(session_id, speaker_uid))
        if subject.isdigit():
            if is_group and gid:
                return owner_group_member(gid, subject)
            return speaker_owner or own_owner(session_id, speaker_uid)
        return speaker_owner or own_owner(session_id, speaker_uid)

    # ── 重置 / 清理 ──────────────────────────────────────
    async def on_session_reset(self, session_id: str, user_id: Any = None) -> str:
        """显式重置会话时的记忆处理（suspend / clear / keep）。"""
        if not self.enabled():
            return "长期记忆未启用"
        mode = str(self._get("memory_on_reset", "suspend") or "suspend").strip().lower()
        owner = self.own_owner(session_id, user_id)
        try:
            if mode == "clear":
                count = await asyncio.to_thread(self.store.clear, owner)
                await self._audit("clear", owner=owner, user_id=str(user_id or ""),
                                  summary=f"on_session_reset clear {owner}", source="reset")
                return f"已清除 {count} 条个人记忆"
            if mode in ("suspend", ""):
                await asyncio.to_thread(self.store.set_reset, owner)
                await self._audit("reset", owner=owner, user_id=str(user_id or ""),
                                  summary=f"on_session_reset suspend {owner}", source="reset")
                return "已重置记忆上下文（旧记忆挂起）"
            return "记忆保持现状（keep）"
        except Exception as e:  # noqa: BLE001 —— 重置失败要能说清，而不是静默
            self._log("warning")(f"[Memory] 会话重置处理异常: {e}")
            return "重置处理失败"

    def delete_own(self, session_id: str, user_id: Any, target: str) -> int:
        """只删本人层记忆：优先 id，其次词匹配。"""
        owner = self.own_owner(session_id, user_id)
        try:
            if self.store.get_owned(target, owner):
                self.store.delete_fact(target, owner=owner)
                if bool(self._get("memory_audit_enable", True)):
                    # 审计不落原文：用户说「忘掉」之后，正文不该继续留在审计表里
                    self.store.audit("forget", owner=owner, ref_id=target,
                                     user_id=str(user_id or ""),
                                     summary=f"id={target}", source="tool")
                return 1
            deleted = self.store.delete_by_query(owner, target)
            if deleted and bool(self._get("memory_audit_enable", True)):
                self.store.audit("forget", owner=owner, user_id=str(user_id or ""),
                                 summary=f"query={target}", source="tool")
            return deleted
        except Exception as e:  # noqa: BLE001
            self._log("warning")(f"[Memory] 删除失败: {e}")
            return 0

    def deny_own(self, session_id: str, user_id: Any, target: str) -> int:
        try:
            return self.store.deny(self.own_owner(session_id, user_id), target)
        except Exception as e:  # noqa: BLE001
            self._log("warning")(f"[Memory] 下架失败: {e}")
            return 0

    def confirm_own(self, session_id: str, user_id: Any, target: str) -> int:
        try:
            return self.store.confirm(self.own_owner(session_id, user_id), target)
        except Exception as e:  # noqa: BLE001
            self._log("warning")(f"[Memory] 确认失败: {e}")
            return 0

    def correct_own(self, session_id: str, user_id: Any, old: str, new: str) -> str | None:
        try:
            return self.store.correct(
                self.own_owner(session_id, user_id), old, new, source_user=str(user_id or "")
            )
        except Exception as e:  # noqa: BLE001
            self._log("warning")(f"[Memory] 纠正失败: {e}")
            return None

    # ── embedding 回填 ───────────────────────────────────
    async def backfill_embeddings(self, limit: int = 50) -> int:
        """给缺失向量的记忆补齐 embedding；不可用/失败返回 0。"""
        client = self.embedding_client()
        if not await client.available():
            return 0
        try:
            rows = await asyncio.to_thread(self.store.missing_embeddings, limit)
        except Exception as e:  # noqa: BLE001
            self._log("debug")(f"[Memory] 读取缺失向量列表失败: {e}")
            return 0
        if not rows:
            return 0
        ids = [str(r.get("id") or "") for r in rows]
        texts = [str(r.get("content") or "") for r in rows]
        try:
            vectors = await client.embed(texts)
        except Exception as e:  # noqa: BLE001 —— 回填失败下次再试
            self._log("warning")(f"[Memory] Embedding 回填失败（下次重试）: {e}")
            return 0
        if len(vectors) != len(ids):
            self._log("warning")(f"[Memory] Embedding 回填数量不匹配：{len(vectors)}/{len(ids)}")
            return 0
        model_name = str((client.describe() or {}).get("model") or "")
        count = 0
        for mid, vector in zip(ids, vectors, strict=False):
            if not vector:
                continue
            try:
                await asyncio.to_thread(
                    self.store.set_embedding, mid, vector,
                    model=model_name, dim=len(vector),
                )
                count += 1
            except Exception as e:  # noqa: BLE001 —— 单条失败跳过
                self._log("debug")(f"[Memory] 写入向量失败（跳过）: {e}")
        return count

    async def embedding_worker(self, *, interval_s: int = 120) -> None:
        """后台补齐缺失向量（模块 on_load 时 spawn）。"""
        await asyncio.sleep(10)
        while not self._stopped:
            try:
                if self.enabled():
                    await self.backfill_embeddings(50)
            except Exception as e:  # noqa: BLE001 —— 后台循环必须自愈
                self._log("warning")(f"[Memory] 向量回填循环异常（继续）: {e}")
            await asyncio.sleep(max(30, int(interval_s)))

    def start_worker(self) -> asyncio.Task | None:
        """启动 embedding worker（若调用方没有 spawn 能力时使用）。"""
        if self._worker_task is not None and not self._worker_task.done():
            return self._worker_task
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 没有事件循环（加载期/测试替身）：交给 on_load 的 spawn 或下次 backfill
            self._log("debug")("[Memory] 无事件循环，跳过自管 embedding worker")
            return None
        try:
            self._worker_task = loop.create_task(
                self.embedding_worker(), name=f"memory-embeddings:{self.bot_id}"
            )
            return self._worker_task
        except Exception as e:  # noqa: BLE001 —— 创建任务失败不应影响记忆主流程
            self._log("debug")(f"[Memory] 启动向量回填 worker 失败（已忽略）: {e}")
            return None

    async def llm_chat(self, messages: list[dict], **kwargs: Any) -> Any:
        """转调模块的框架 LLM 客户端（密钥/回退/记账都留在框架）。"""
        chat = getattr(self.module, "llm_chat", None)
        if not callable(chat):
            raise RuntimeError("模块不支持 llm_chat（无法蒸馏）")
        return await chat(messages, **kwargs)

    # ── 生命周期 ─────────────────────────────────────────
    def stop(self) -> None:
        self._stopped = True
        if self._worker_task is not None and not self._worker_task.done():
            self._worker_task.cancel()
        self._worker_task = None
        self.consolidator.stop()
        try:
            self.store.close()
        except Exception as e:  # noqa: BLE001
            self._log("debug")(f"[Memory] 关闭记忆存储失败（已忽略）: {e}")
