"""LLM 巩固流水线（设计契约 §5.5）：episodes → 抽取 ops → 写库 → 标记已巩固。

流程（与契约逐条对应）：
1. 门禁：``manager.enabled()`` + ``manager.scene_enabled(session_id)`` +
   ``memory_extract_enable``；限频 ``memory_extract_interval_min``（``force`` 跳过）；
2. 取未巩固 episode（``memory_consolidate_batch`` 条），累计不足
   ``memory_consolidate_min_chars`` 字直接返回 0；
3. 按 owner（说话人）分组：一组一次 LLM 请求（说话人自述才有正确的 ``subject=self``），
   每组用 ``manager.candidates_for_extraction`` 取已有记忆做 UPDATE/SUPERSEDE 上下文；
4. ``manager.llm_chat`` 失败（``resp.ok`` 为假 / 抛异常）→ warning，**不 mark**，下次重试；
5. ``parse_ops`` 逐条 ``manager.apply_op``（带说话人上下文），单条失败只 warning；
   LLM 成功那组写完后 ``mark_episodes_consolidated``；
6. 收尾 ``store.audit("consolidate", ...)`` + ``store.prune_episodes()``。

并发：``run`` 本身是 async，由 manager 用 ``module.spawn`` 托管（这里**不**自己建 task）；
``_last_run`` / ``_running`` 用 ``threading.Lock`` 保护，限频检查可被任意协程/线程触发。
"""

from __future__ import annotations

import asyncio
import inspect
import threading
import time
from collections import OrderedDict
from typing import Any

from app.agent import logger
from app.core.logger import account_label

from .extract import EXTRACT_SYSTEM_PROMPT, parse_ops, render_extract_prompt

#: 单轮最多处理多少个说话人分组（控制 LLM 调用成本；其余留到下一轮）
_MAX_GROUPS = 4
#: 限频默认值（分钟）：与配置缺省一致，manager 没给配置时的兜底
_DEFAULT_INTERVAL_MIN = 10.0
#: 每轮最多取多少条 episode
_DEFAULT_BATCH = 20
#: 语料最少字数（不足不调模型）
_DEFAULT_MIN_CHARS = 30
#: 抽取请求参数（低温 + 小上限：只要结构化 JSON）
_EXTRACT_TEMPERATURE = 0.1
_EXTRACT_MAX_TOKENS = 500


class Consolidator:
    """后台巩固器：把某会话的未巩固 episode 交给 LLM 抽成记忆操作并落库。

    典型用法（manager 侧）::

        consolidator = Consolidator(module, manager)
        if consolidator.should_run(session_id):
            module.spawn(consolidator.run(session_id, is_group=is_group))
    """

    def __init__(self, module: Any, manager: Any) -> None:
        self.module = module
        self.manager = manager
        self.bot_id = getattr(module, "bot_id", "") or getattr(manager, "bot_id", "")
        self._lock = threading.Lock()
        self._last_run: dict[str, float] = {}
        self._running: set[str] = set()
        self._stopped = False

    # ── 日志 / 配置 ─────────────────────────────────────────
    def _log(self):
        return logger.add_info(account_label(self.bot_id))

    def _cfg(self, key: str, default: Any) -> Any:
        """读配置：manager.get → manager._get → 默认值（新老 manager 都能跑）。"""
        for name in ("get", "_get"):
            getter = getattr(self.manager, name, None)
            if not callable(getter):
                continue
            try:
                return getter(key, default)
            except Exception as e:  # noqa: BLE001 —— 读配置失败按默认值继续
                self._log().debug(f"[记忆] 读取配置 {key} 失败（用默认值）: {e}")
        return default

    def _cfg_float(self, key: str, default: float) -> float:
        """读数值配置；非数值时用默认值并 debug 留痕。"""
        raw = self._cfg(key, default)
        try:
            return float(raw)
        except (TypeError, ValueError):
            self._log().debug(f"[记忆] 配置 {key} 不是数值（{raw!r}），按默认 {default} 处理")
            return default

    def _cfg_int(self, key: str, default: int) -> int:
        """读整数配置；非数值时用默认值并 debug 留痕。"""
        raw = self._cfg(key, default)
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            self._log().debug(f"[记忆] 配置 {key} 不是整数（{raw!r}），按默认 {default} 处理")
            return default

    # ── 门禁 / 限频 ─────────────────────────────────────────
    def _gates_ok(self, session_id: str) -> bool:
        """总开关 + 场景开关 + 自动蒸馏开关（任一关掉就不跑）。"""
        try:
            if not bool(self.manager.enabled()):
                return False
            if not bool(self.manager.scene_enabled(session_id)):
                return False
        except Exception as e:  # noqa: BLE001 —— 门禁异常按"本轮不跑"处理
            self._log().warning(f"[记忆] 巩固前置检查异常（跳过本轮）: {e}")
            return False
        return bool(self._cfg("memory_extract_enable", False))

    def _interval_elapsed(self, session_id: str) -> bool:
        """距上次巩固是否已过限频窗口。"""
        interval = self._cfg_float("memory_extract_interval_min", _DEFAULT_INTERVAL_MIN) * 60.0
        with self._lock:
            last = self._last_run.get(session_id, 0.0)
        if last <= 0:
            return True
        return (time.time() - last) >= interval

    def should_run(self, session_id: str) -> bool:
        """是否值得触发一次巩固（只做判断，不改状态）；供 manager 决定是否 spawn。"""
        sid = str(session_id or "")
        if not sid or self._stopped:
            return False
        if not self._gates_ok(sid):
            return False
        return self._interval_elapsed(sid)

    def status(self) -> dict:
        """诊断视图：``{"last_run": {session_id: ts}, "running": bool}``。"""
        with self._lock:
            last_run = dict(self._last_run)
            running = bool(self._running)
        return {"last_run": last_run, "running": running}

    def stop(self) -> None:
        """停止巩固器：之后的 ``should_run``/``run`` 一律返回 False/0。"""
        with self._lock:
            self._stopped = True
            self._running.clear()
        self._log().debug("[记忆] 巩固器已停止")

    # ── 主流程 ─────────────────────────────────────────────
    async def run(
        self,
        session_id: str,
        *,
        is_group: bool,
        force: bool = False,
        source: str = "chat",
    ) -> int:
        """执行一次巩固，返回写入（apply_op 成功）条数。

        Args:
            session_id: ``group_x`` / ``private_y``。
            is_group: 群聊 / 私聊（决定 owner 与说话人标签）。
            force: 跳过限频（归档、``/memory`` 手动触发用）；门禁仍生效。
            source: 触发来源（写审计）。

        Returns:
            写入条数（0 表示跳过、无内容或模型失败）。
        """
        sid = str(session_id or "")
        if not sid or self._stopped:
            return 0
        if not self._gates_ok(sid):
            return 0
        if not force and not self._interval_elapsed(sid):
            return 0
        if not self._begin(sid):
            self._log().debug(f"[记忆] 巩固已在运行，跳过重复触发（{sid}）")
            return 0
        try:
            return await self._run_inner(sid, is_group=is_group, source=source)
        except Exception as e:  # noqa: BLE001 —— 巩固失败绝不影响聊天，下轮重试
            self._log().warning(f"[记忆] 巩固失败（session={sid}）: {e}")
            return 0
        finally:
            self._end(sid)

    def _begin(self, session_id: str) -> bool:
        """登记本轮开始；同一会话已在跑则拒绝（防并发重复消耗 token）。"""
        with self._lock:
            if self._stopped or session_id in self._running:
                return False
            self._running.add(session_id)
            self._last_run[session_id] = time.time()
            return True

    def _end(self, session_id: str) -> None:
        """登记本轮结束。"""
        with self._lock:
            self._running.discard(session_id)

    async def _run_inner(self, session_id: str, *, is_group: bool, source: str) -> int:
        """取 episode → 分组巩固；无论成败最后都清理过期 episode。"""
        store = getattr(self.manager, "store", None)
        if store is None:
            self._log().debug("[记忆] 巩固跳过：store 不可用")
            return 0
        try:
            return await self._consolidate(store, session_id, is_group=is_group, source=source)
        finally:
            await self._prune(store)

    async def _consolidate(
        self,
        store: Any,
        session_id: str,
        *,
        is_group: bool,
        source: str,
    ) -> int:
        """实际的巩固逻辑（不含 prune）。"""
        batch = max(1, self._cfg_int("memory_consolidate_batch", _DEFAULT_BATCH))
        episodes = await self._store_call(store, "list_episodes", session_id, limit=batch)
        if not isinstance(episodes, list) or not episodes:
            self._log().debug(f"[记忆] 巩固跳过：没有未巩固语料（{session_id}）")
            return 0

        fetched_rows = [ep for ep in episodes if isinstance(ep, dict) and ep.get("id")]
        user_episodes = [
            ep for ep in fetched_rows
            if str(ep.get("content") or "").strip() and _is_user_role(ep)
        ]
        # 助手消息 / 空内容不会变成用户记忆：直接消费掉，否则会永久占满批次窗口
        consumed_ids = [
            str(ep.get("id")) for ep in fetched_rows
            if not (str(ep.get("content") or "").strip() and _is_user_role(ep))
        ]
        if not user_episodes:
            if consumed_ids:
                await self._store_call(store, "mark_episodes_consolidated", consumed_ids)
            return 0

        total_chars = sum(len(str(ep.get("content") or "").strip()) for ep in user_episodes)
        min_chars = self._cfg_int("memory_consolidate_min_chars", _DEFAULT_MIN_CHARS)
        if total_chars < min_chars:
            self._log().debug(
                f"[记忆] 巩固跳过：语料不足（{total_chars} < {min_chars} 字，{session_id}）"
            )
            if consumed_ids:
                await self._store_call(store, "mark_episodes_consolidated", consumed_ids)
            return 0

        fetched = len(fetched_rows)
        written = 0
        marked = 0
        for owner_key, group in _group_by_owner(user_episodes):
            result = await self._consolidate_group(
                store, session_id, is_group=is_group, owner_key=owner_key, group=group,
            )
            if result is None:
                # 模型失败/未就绪：这组 episode 保持未巩固，下一轮重试
                continue
            written += result["written"]
            ids = [str(ep.get("id")) for ep in group if ep.get("id")]
            if ids:
                await self._store_call(store, "mark_episodes_consolidated", ids)
                marked += len(ids)

        if consumed_ids:
            await self._store_call(store, "mark_episodes_consolidated", consumed_ids)
            marked += len(consumed_ids)

        if marked:
            await self._audit(store, session_id, source, written=written, marked=marked, fetched=fetched)
        return written

    async def _consolidate_group(
        self,
        store: Any,
        session_id: str,
        *,
        is_group: bool,
        owner_key: str,
        group: list[dict],
    ) -> dict | None:
        """对同一说话人的一组 episode 做一次抽取；失败返回 None（不 mark）。"""
        lines = [str(ep.get("content") or "").strip() for ep in group]
        text = "\n".join(lines)
        first = group[0]
        speaker_uid = str(first.get("user_id") or "")
        speaker_owner = str(first.get("owner") or "") or owner_key
        speaker_label = (
            f"群成员 QQ:{speaker_uid}" if is_group and speaker_uid
            else f"私聊用户 QQ:{speaker_uid}" if speaker_uid
            else f"群成员 {speaker_owner}" if is_group
            else f"用户 {speaker_owner}"
        )

        existing = self._candidates(session_id, is_group, text)
        prompt = render_extract_prompt(speaker_label, lines, existing)
        resp = await self._llm(prompt)
        if resp is None or not bool(getattr(resp, "ok", False)):
            self._log().warning(
                f"[记忆] 巩固模型调用失败（session={session_id}，owner={speaker_owner}）："
                "本轮不标记，下次重试"
            )
            return None

        existing_ids = {
            str(row.get("id")) for row in existing if isinstance(row, dict) and row.get("id")
        }
        ops = parse_ops(
            str(getattr(resp, "text", "") or ""),
            existing_ids=existing_ids,
            source_text=text,
        )
        written = 0
        for op in ops:
            try:
                mid = await self.manager.apply_op(
                    op,
                    session_id=session_id,
                    is_group=is_group,
                    speaker_uid=speaker_uid,
                    speaker_owner=speaker_owner,
                )
            except Exception as e:  # noqa: BLE001 —— 单条失败只跳过该条
                self._log().warning(f"[记忆] 应用记忆操作失败（已跳过该条）: {e}")
                continue
            if mid:
                written += 1
        return {"written": written}

    # ── 依赖调用（都做容错 + 留痕） ────────────────────────
    def _candidates(self, session_id: str, is_group: bool, text: str) -> list[dict]:
        """取已有记忆候选（供 UPDATE/SUPERSEDE 引用）；异常按空列表处理。"""
        getter = getattr(self.manager, "candidates_for_extraction", None)
        if not callable(getter):
            return []
        try:
            rows = getter(session_id, is_group, text)
        except Exception as e:  # noqa: BLE001 —— 取候选失败只降低抽取质量
            self._log().warning(f"[记忆] 取候选记忆失败（按空处理）: {e}")
            return []
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]

    async def _llm(self, prompt: str) -> Any:
        """调 manager.llm_chat（缺失时退回 module.llm_chat）；异常返回 None。"""
        messages = [
            {"role": "system", "content": EXTRACT_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        chat = getattr(self.manager, "llm_chat", None)
        if not callable(chat):
            chat = getattr(self.module, "llm_chat", None)
        if not callable(chat):
            self._log().warning("[记忆] 巩固跳过：manager/module 都没有 llm_chat")
            return None
        try:
            return await chat(
                messages,
                temperature=_EXTRACT_TEMPERATURE,
                max_tokens=_EXTRACT_MAX_TOKENS,
            )
        except Exception as e:  # noqa: BLE001 —— 模型请求失败走"下轮重试"
            self._log().warning(f"[记忆] 巩固模型请求异常（下次重试）: {e}")
            return None

    async def _store_call(self, store: Any, name: str, *args: Any, **kwargs: Any) -> Any:
        """调 store 的同步方法（放线程池，避免 SQLite 阻塞事件循环）。

        同时兼容异步 fake：方法是 coroutine function 时直接 await。
        """
        method = getattr(store, name, None)
        if not callable(method):
            self._log().debug(f"[记忆] store 缺少方法 {name}（跳过）")
            return None
        try:
            if inspect.iscoroutinefunction(method):
                return await method(*args, **kwargs)
            return await asyncio.to_thread(method, *args, **kwargs)
        except Exception as e:  # noqa: BLE001 —— 单步 DB 失败不能让整轮巩固崩掉
            self._log().warning(f"[记忆] 巩固时调用 store.{name} 失败: {e}")
            return None

    async def _audit(
        self,
        store: Any,
        session_id: str,
        source: str,
        *,
        written: int,
        marked: int,
        fetched: int,
    ) -> None:
        """写一条 consolidate 审计（失败只 debug，不影响巩固结果）。"""
        await self._store_call(
            store,
            "audit",
            "consolidate",
            owner=session_id,
            summary=f"ops={written} episodes={marked}/{fetched}",
            source=source,
        )

    async def _prune(self, store: Any) -> None:
        """清理过期 / 超量的 episode（保留策略见 store）。"""
        method = getattr(store, "prune_episodes", None)
        if not callable(method):
            return
        try:
            if inspect.iscoroutinefunction(method):
                await method()
            else:
                await asyncio.to_thread(method)
        except Exception as e:  # noqa: BLE001 —— 清理失败不影响记忆可用性
            self._log().debug(f"[记忆] 清理过期 episode 失败（忽略）: {e}")


def _is_user_role(episode: dict) -> bool:
    """是否用户消息（助手/系统消息不作为用户记忆来源）。"""
    role = str(episode.get("role") or "user").strip().lower()
    return role in ("user", "")


def _group_by_owner(episodes: list[dict]) -> list[tuple[str, list[dict]]]:
    """按说话人 owner 分组（同组一次 LLM 请求）；超过 ``_MAX_GROUPS`` 只取最大的几组。"""
    groups: OrderedDict[str, list[dict]] = OrderedDict()
    for episode in episodes:
        owner = str(episode.get("owner") or "").strip()
        uid = str(episode.get("user_id") or "").strip()
        key = owner or (f"uid:{uid}" if uid else "unknown")
        groups.setdefault(key, []).append(episode)
    ordered = sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))
    return ordered[:_MAX_GROUPS]
