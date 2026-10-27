"""作用域绑定与群响应策略的存储/解析实现（`ConfigService` 的 mixin）。

拆出原因：绑定逻辑是独立的存储关注点，且 `ConfigService` 已触到单文件 800 行的架构预算
（`tests/architecture/test_architecture_layout.py`）。本模块由 `ConfigService` 继承使用，
不直接实例化；宿主需提供 ``db / log / _webui / _bindings / _channel_policies / _notify /
_schedule_persist``。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from typing import Any

from app.domain.binding import (
    SCOPE_ACCOUNT,
    SCOPE_GLOBAL,
    SCOPES,
    Binding,
    BindingResolver,
    ResolvedBinding,
)

#: ``set_binding`` 的「本参数未提供」哨兵（区分「不改」与「显式写 None/False」）
_UNSET = object()

#: 框架级能力：**不参与「新账号模块默认关」**。
#:
#: Agent 是框架自身的回复能力——把它也关掉，新账号配完向导后会完全静默
#: （正是 P0 要消灭的「没反应」）。要静默请用 Agent 自己的开关。
CORE_CAPABILITIES: tuple[str, ...] = ("agent",)


class BindingStoreMixin:
    """作用域绑定（bindings）+ 群响应权策略（channel_policies）的读写与迁移。"""

    # ==================== 作用域绑定（bindings） ====================
    # 唯一存储：capability × (global|account|channel) → enabled/config/permission。
    # 旧的 module_config / module_authority 只在迁移期读，运行时不再写（docs/multi-account-design.md §4）。

    def list_bindings(self, capability_id: str) -> list[dict]:
        """返回某能力的全部 binding（深拷贝，按作用域从宽到窄排序）。"""
        rows = self._bindings.get(str(capability_id), {})
        scope_order = {scope: index for index, scope in enumerate(SCOPES)}
        ordered = sorted(rows.items(), key=lambda item: (scope_order.get(item[0][0], 99), item[0][1]))
        return [
            {
                "capability_id": str(capability_id),
                "scope_type": scope_type,
                "scope_id": scope_id,
                "enabled": row.get("enabled"),
                "config": dict(row.get("config") or {}),
                "permission": dict(row.get("permission") or {}),
                "priority": int(row.get("priority", 100)),
            }
            for (scope_type, scope_id), row in ordered
        ]

    def _binding_objects(self, capability_id: str) -> list[Binding]:
        """把存储行转成解析器需要的 ``Binding`` 值对象。"""
        return [
            Binding(
                capability_id=row["capability_id"],
                scope_type=row["scope_type"],
                scope_id=row["scope_id"],
                enabled=row["enabled"],
                config=row["config"],
                permission=row["permission"],
                priority=row["priority"],
            )
            for row in self.list_bindings(capability_id)
        ]

    def resolve_binding(
        self,
        capability_id: str,
        *,
        account_id: Any = None,
        channel_id: Any = None,
        default_enabled: bool = True,
        default_config: dict | None = None,
        default_permission: dict | None = None,
    ) -> ResolvedBinding:
        """按 global → account → channel 解析有效配置（最具体者胜）。"""
        resolver = BindingResolver(self._binding_objects, default_enabled=default_enabled)
        return resolver.resolve(
            str(capability_id),
            account_id=None if account_id is None else str(account_id),
            channel_id=None if channel_id is None else str(channel_id),
            default_config=default_config,
            default_permission=default_permission,
        )

    def get_binding_view(self, capability_id: str, *, account_id: Any = None,
                         channel_id: Any = None) -> dict:
        """解析结果 + 每个配置键的来源作用域（WebUI 显示继承链用）。"""
        resolved = self.resolve_binding(capability_id, account_id=account_id, channel_id=channel_id)
        return {
            "capability_id": resolved.capability_id,
            "account_id": resolved.account_id,
            "channel_id": resolved.channel_id,
            "enabled": resolved.enabled,
            "enabled_source": resolved.enabled_source,
            "config": dict(resolved.config),
            "permission": dict(resolved.permission),
            "sources": dict(resolved.sources),
        }

    def get_binding(self, capability_id: str, scope_type: str, scope_id: Any = "") -> dict | None:
        row = self._bindings.get(str(capability_id), {}).get((scope_type, str(scope_id or "")))
        if row is None:
            return None
        return {
            "capability_id": str(capability_id),
            "scope_type": scope_type,
            "scope_id": str(scope_id or ""),
            "enabled": row.get("enabled"),
            "config": dict(row.get("config") or {}),
            "permission": dict(row.get("permission") or {}),
            "priority": int(row.get("priority", 100)),
        }

    def set_binding(
        self,
        capability_id: str,
        scope_type: str,
        scope_id: Any = "",
        *,
        enabled: Any = _UNSET,
        config: Any = _UNSET,
        permission: Any = _UNSET,
        priority: Any = _UNSET,
        persist: bool = True,
    ) -> dict:
        """写一条 binding（只更新显式传入的字段，其余保持原样）。"""
        if scope_type not in SCOPES:
            raise ValueError(f"未知 binding 作用域: {scope_type!r}")
        capability_id = str(capability_id)
        key = (scope_type, str(scope_id or ""))
        rows = self._bindings.setdefault(capability_id, {})
        row = rows.setdefault(
            key, {"enabled": None, "config": {}, "permission": {}, "priority": 100}
        )
        if enabled is not _UNSET:
            row["enabled"] = None if enabled is None else bool(enabled)
        if config is not _UNSET:
            row["config"] = dict(config or {})
        if permission is not _UNSET:
            row["permission"] = dict(permission or {})
        if priority is not _UNSET:
            row["priority"] = int(priority)
        if persist:
            self._schedule_persist(self._persist_binding, capability_id, key)
        return self.get_binding(capability_id, scope_type, scope_id) or {}

    async def save_binding(
        self,
        capability_id: str,
        scope_type: str,
        scope_id: Any = "",
        **fields: Any,
    ) -> dict:
        """写 binding + 落库 + 广播（``fields`` 同 :meth:`set_binding`）。"""
        data = self.set_binding(capability_id, scope_type, scope_id, persist=False, **fields)
        await self._persist_binding(str(capability_id), (scope_type, str(scope_id or "")))
        await self._notify(
            "bindings",
            {"capability_id": str(capability_id), "scope_type": scope_type,
             "scope_id": str(scope_id or ""), "binding": data},
        )
        return data

    async def _persist_binding(self, capability_id: str, key: tuple[str, str]) -> None:
        try:
            row = self._bindings.get(capability_id, {}).get(key)
            if row is None:
                await self.db.execute(
                    "DELETE FROM bindings WHERE capability_id = ? AND scope_type = ? AND scope_id = ?",
                    (capability_id, key[0], key[1]),
                )
                return
            enabled = row.get("enabled")
            await self.db.execute(
                "INSERT OR REPLACE INTO bindings "
                "(capability_id, scope_type, scope_id, enabled, config_json, permission_json, priority, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    capability_id, key[0], key[1],
                    None if enabled is None else (1 if enabled else 0),
                    self.db.dumps(row.get("config") or {}),
                    self.db.dumps(row.get("permission") or {}),
                    int(row.get("priority", 100)),
                    int(asyncio.get_running_loop().time()),
                ),
            )
        except Exception as e:
            self.log.error(f"[Config] 持久化 binding 失败 {capability_id}/{key}: {e}")

    async def _load_bindings(self) -> None:
        rows = await self.db.fetchall("SELECT * FROM bindings")
        self._bindings = {}
        for r in rows:
            enabled = r["enabled"]
            self._bindings.setdefault(str(r["capability_id"]), {})[
                (str(r["scope_type"]), str(r["scope_id"] or ""))
            ] = {
                "enabled": None if enabled is None else bool(enabled),
                "config": self.db.loads(r["config_json"], {}) or {},
                "permission": self.db.loads(r["permission_json"], {}) or {},
                "priority": int(r["priority"] or 100),
            }

    async def _migrate_bindings_if_needed(self) -> bool:
        """一次性迁移：旧两张表 + 旧 single_service/multi_group → bindings/channel_policies。

        幂等：``kv["bindings_migrated"]="1"`` 之后直接跳过；旧表保留为回滚快照。
        """
        flag = await self.db.fetchone(
            "SELECT value_json FROM kv WHERE key = ?", ("bindings_migrated",)
        )
        if flag is not None and self.db.loads(flag["value_json"], "") == "1":
            return False

        merged: dict[tuple[str, str, str], dict] = {}

        def row_for(capability_id: str, scope_type: str, scope_id: str) -> dict:
            return merged.setdefault(
                (str(capability_id), scope_type, str(scope_id or "")),
                {"enabled": None, "config": {}, "permission": {}, "priority": 100},
            )

        for record in await self.db.fetchall(
            "SELECT module_name, bot_id, config_json FROM module_config"
        ):
            bot_id = record["bot_id"]
            scope_type, scope_id = (SCOPE_GLOBAL, "") if not bot_id else (SCOPE_ACCOUNT, str(bot_id))
            row_for(record["module_name"], scope_type, scope_id)["config"] = (
                self.db.loads(record["config_json"], {}) or {}
            )

        for record in await self.db.fetchall("SELECT * FROM module_authority"):
            bot_id = record["bot_id"]
            scope_type, scope_id = (SCOPE_GLOBAL, "") if not bot_id else (SCOPE_ACCOUNT, str(bot_id))
            row = row_for(record["module_name"], scope_type, scope_id)
            row["enabled"] = bool(record["enabled"])
            row["permission"] = {
                "group_mode": record["group_mode"],
                "group_list": self.db.loads(record["group_list"], []),
                "user_mode": record["user_mode"],
                "user_list": self.db.loads(record["user_list"], []),
            }

        now = int(time.time())
        sqls: list[tuple[str, Sequence[Any]]] = []
        for (capability_id, scope_type, scope_id), row in merged.items():
            enabled = row["enabled"]
            sqls.append((
                "INSERT OR IGNORE INTO bindings "
                "(capability_id, scope_type, scope_id, enabled, config_json, permission_json, priority, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    capability_id, scope_type, scope_id,
                    None if enabled is None else int(bool(enabled)),
                    self.db.dumps(row["config"]),
                    self.db.dumps(row["permission"]),
                    int(row["priority"]),
                    now,
                ),
            ))

        # 旧「单一服务」：开关 + 群级服务号 → delegate 策略（未指定服务号的群保持旧行为=不过滤）
        policy_count = 0
        single_service = self._webui.get("single_service") or {}
        groups = (self._webui.get("multi_group") or {}).get("groups") or {}
        for capability_id, enabled in single_service.items():
            if not enabled:
                continue
            for channel_id, group_config in groups.items():
                primary = (group_config or {}).get("service_bot_id")
                if primary in (None, ""):
                    continue
                policy_count += 1
                sqls.append((
                    "INSERT OR IGNORE INTO channel_policies "
                    "(capability_id, channel_id, policy, primary_account_id, fallback, updated_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (str(capability_id), str(channel_id), "delegate", str(primary), "open", now),
                ))

        sqls.append((
            "INSERT OR REPLACE INTO kv (key, value_json) VALUES (?,?)",
            ("bindings_migrated", self.db.dumps("1")),
        ))
        await self.db.run_in_transaction(sqls)
        await self._load_bindings()
        await self._load_channel_policies()
        self.log.info(
            f"[Config] 已迁移作用域绑定: bindings={len(merged)} 群响应策略={policy_count}"
        )
        return True

    # ==================== 群响应权策略（channel_policies） ====================

    def list_channel_policies(self, capability_id: str | None = None) -> list[dict]:
        rows = [
            dict(row)
            for (cap, _channel_id), row in self._channel_policies.items()
            if capability_id is None or cap == str(capability_id)
        ]
        return sorted(rows, key=lambda row: (row["capability_id"], row["channel_id"]))

    def get_channel_policy(self, capability_id: str, channel_id: Any) -> dict | None:
        row = self._channel_policies.get((str(capability_id), str(channel_id)))
        return dict(row) if row is not None else None

    async def save_channel_policies(self, capability_id: str, policies: list[dict]) -> list[dict]:
        """整值替换某能力的全部群策略（空列表 = 清空，回到 all 语义）。"""
        capability_id = str(capability_id)
        normalized: list[dict] = []
        for item in policies or []:
            channel_id = str(item.get("channel_id") or item.get("group_id") or "").strip()
            if not channel_id:
                continue
            primary = item.get("primary_account_id")
            normalized.append({
                "capability_id": capability_id,
                "channel_id": channel_id,
                "policy": str(item.get("policy") or "all").strip().lower(),
                "primary_account_id": None if primary in (None, "") else str(primary),
                "fallback": str(item.get("fallback") or "open").strip().lower(),
            })

        for key in [key for key in self._channel_policies if key[0] == capability_id]:
            self._channel_policies.pop(key, None)
        for row in normalized:
            self._channel_policies[(capability_id, row["channel_id"])] = dict(row)

        now = int(time.time())
        sqls: list[tuple[str, Sequence[Any]]] = [
            ("DELETE FROM channel_policies WHERE capability_id = ?", (capability_id,))
        ]
        for row in normalized:
            sqls.append((
                "INSERT OR REPLACE INTO channel_policies "
                "(capability_id, channel_id, policy, primary_account_id, fallback, updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (capability_id, row["channel_id"], row["policy"],
                 row["primary_account_id"], row["fallback"], now),
            ))
        await self.db.run_in_transaction(sqls)
        await self._notify(
            "channel_policies", {"capability_id": capability_id, "policies": normalized}
        )
        return normalized

    async def _load_channel_policies(self) -> None:
        rows = await self.db.fetchall("SELECT * FROM channel_policies")
        self._channel_policies = {}
        for r in rows:
            row = {
                "capability_id": str(r["capability_id"]),
                "channel_id": str(r["channel_id"]),
                "policy": str(r["policy"]),
                "primary_account_id": (
                    str(r["primary_account_id"]) if r["primary_account_id"] not in (None, "") else None
                ),
                "fallback": str(r["fallback"]),
            }
            self._channel_policies[(row["capability_id"], row["channel_id"])] = row

    # ==================== 模块配置（兼容门面，读写 bindings） ====================

    def get_module_config(self, module: str, bot_id: Any) -> dict:
        """账号级模块配置（继承 global）；``bot_id=None`` 只取 global 层。"""
        resolved = self.resolve_binding(module, account_id=bot_id)
        return dict(resolved.config)

    def get_all_module_configs(self, module: str) -> dict[str, dict]:
        """账号作用域的配置快照（跨账号批量写的老 API 仍按此结构消费）。"""
        rows = self._bindings.get(str(module), {})
        return {
            scope_id: dict(row.get("config") or {})
            for (scope_type, scope_id), row in rows.items()
            if scope_type == SCOPE_ACCOUNT and scope_id
        }

    def set_module_config(self, module: str, bot_id: Any, config: dict, persist: bool = True) -> None:
        scope_type, scope_id = self._legacy_scope(bot_id)
        self.set_binding(
            module, scope_type, scope_id, config=dict(config or {}), persist=persist
        )

    async def save_module_config(self, module: str, bot_id: Any, config: dict) -> None:
        scope_type, scope_id = self._legacy_scope(bot_id)
        await self.save_binding(module, scope_type, scope_id, config=dict(config or {}))
        await self._notify(
            "module_config", {"module": module, "account_id": bot_id, "config": dict(config)}
        )

    @staticmethod
    def _legacy_scope(bot_id: Any) -> tuple[str, str]:
        return (SCOPE_GLOBAL, "") if bot_id is None else (SCOPE_ACCOUNT, str(bot_id))

    # ==================== 模块权限（兼容门面，读写 bindings） ====================

    def module_default_enabled(self, module: Any, bot_id: Any) -> bool:
        """该模块在该账号上的「默认开关」（没有任何 binding 覆盖时的值）。

        新账号 = False：未显式打开的模块一律不响应（用户加号 ≠ 全功能上线）。
        老账号 = True：升级后存量账号行为不变（见 ``account_store.get_modules_default_enabled``）。
        Agent 与全球层（``bot_id is None``）恒 True：它们是框架/全局语义，不是"某个新账号的模块"。
        """
        if bot_id is None or str(module) in CORE_CAPABILITIES:
            return True
        return self.get_modules_default_enabled(bot_id)

    def get_module_authority(self, module: str, bot_id: Any) -> dict:
        resolved = self.resolve_binding(
            module,
            account_id=bot_id,
            default_enabled=self.module_default_enabled(module, bot_id),
        )
        data = {"enabled": bool(resolved.enabled)}
        data.update(dict(resolved.permission))
        return data

    def get_default_authority(self, module: str) -> dict:
        """global 层的权威配置（无 global 行时返回默认值）。"""
        data = {
            "enabled": True,
            "group_mode": "blacklist",
            "group_list": [],
            "user_mode": "blacklist",
            "user_list": [],
        }
        data.update(self.get_module_authority(module, None))
        return data

    def set_module_authority(self, module: str, bot_id: Any, authority: dict) -> None:
        scope_type, scope_id = self._legacy_scope(bot_id)
        permission = {
            key: authority[key]
            for key in ("group_mode", "group_list", "user_mode", "user_list")
            if key in authority
        }
        self.set_binding(
            module, scope_type, scope_id,
            enabled=authority.get("enabled", True),
            permission=permission,
            persist=True,
        )

    async def save_module_authority(self, module: str, bot_id: Any, authority: dict) -> None:
        scope_type, scope_id = self._legacy_scope(bot_id)
        permission = {
            key: authority[key]
            for key in ("group_mode", "group_list", "user_mode", "user_list")
            if key in authority
        }
        await self.save_binding(
            module, scope_type, scope_id,
            enabled=authority.get("enabled", True),
            permission=permission,
        )
        await self._notify(
            "authority", {"module": module, "account_id": bot_id, "authority": dict(authority)}
        )
