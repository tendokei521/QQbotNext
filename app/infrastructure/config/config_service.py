"""配置中心：所有配置的单一入口。

- SQLite 落盘 + 内存缓存 + 变更通知（替代原 watchdog 文件监听）；
- 首次启动自动从旧 JSON 文件（webserver/webconfig.json、webui/webui_config.json、
  data/module/configs/*/config.json、data/module/configs/*/authority.json）迁移。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.core.logger import logger
from app.infrastructure.config.account_store import AccountStoreMixin
from app.infrastructure.config.binding_store import BindingStoreMixin
from app.infrastructure.config.channel_override_store import ChannelOverrideStoreMixin
from app.infrastructure.config.group_store import GroupStoreMixin
from app.infrastructure.config.ws_url import (
    ACCESS_TOKEN_MASK,
    join_ws_url,
    mask_ws_url,
    restore_ws_url,
    split_ws_url,
)
from app.infrastructure.onebot.endpoint import normalize_mode
from app.infrastructure.persistence.account_migration import (
    migrate_account_identity,
    normalize_account_name,
)
from app.infrastructure.persistence.database import Database

#: 这四个名字历史上就在这里定义，外部（gateway / webui）从本模块导入。
#: 现在实现搬到了 `ws_url.py`，这里保留重导出；`__all__` 让 linter 知道它们是公共 API。
__all__ = [
    "ACCESS_TOKEN_MASK",
    "ConfigService",
    "mask_ws_url",
    "join_ws_url",
    "restore_ws_url",
    "split_ws_url",
]

Listener = Callable[[str, Any], Awaitable[None]]

DEFAULT_WEBUI_CONFIG: dict = {
    "logs": {"show_raw_logs": False, "visible_levels": ["info", "warning", "error"], "max_lines": 50, "console_height": 200},
    "single_service": {},
    "multi_group": {"show_all": False, "groups": {}},
    "experimental": {"show_experimental": False},
}

DEFAULT_PROVIDER_SETTINGS: dict = {
    "default_preset_id": "",
    "default_model_id": "",
    "fallback_model_ids": [],
    "provider_pool": ["*"],
}


class ConfigService(AccountStoreMixin, BindingStoreMixin, GroupStoreMixin, ChannelOverrideStoreMixin):
    def __init__(self, db: Database, project_root: Path, log=None) -> None:
        self.db = db
        self.root = project_root
        self.log = log or logger
        self._listeners: list[Listener] = []

        # 内存缓存（source of truth for 读取）
        # 账号：account_id -> {id, name, ws_url, owner_id, auto_connect, conn_mode}
        # 用 dict 保序（Python 3.7+ 插入序 = 界面顺序），排序另存 sort_order 供重启后还原。
        self._bots: dict[str, dict] = {}
        # 每个账号上一次成功登录的账号快照：{"user_id": str, "nickname": str, "last_login_at": int}
        # 断开后运行时状态会清空，前端据此回退展示；连接成功时由 gateway 回调刷新。
        self._bot_accounts: dict[str, dict] = {}
        self._webui: dict = dict(DEFAULT_WEBUI_CONFIG)
        # 作用域绑定：capability -> {(scope_type, scope_id): row}
        # row = {"enabled": bool|None, "config": dict, "permission": dict, "priority": int}
        self._bindings: dict[str, dict[tuple[str, str], dict]] = {}
        # 群响应权策略：(capability_id, channel_id) -> row
        self._channel_policies: dict[tuple[str, str], dict] = {}
        # 群列表缓存：(account_id, group_id) -> row（离线可看"上次有哪些群"）
        self._known_groups: dict[tuple[str, str], dict] = {}
        # 群级覆盖（对话策略）：(account_id, capability, channel_id) -> row
        self._channel_overrides: dict[tuple[str, str, str], dict] = {}
        self._provider_presets: dict[str, dict] = {}              # preset_id -> preset
        self._provider_models: dict[str, dict] = {}               # model_id -> model
        self._provider_settings: dict = dict(DEFAULT_PROVIDER_SETTINGS)
        self._role_presets: dict[str, dict] = {}                  # role_id -> role preset

    # ==================== 生命周期 ====================
    async def init(self) -> None:
        """初始化：账号身份迁移 → 建表/补列 → 旧 JSON 导入 → 加载 → 绑定向迁移。

        顺序有讲究（docs/multi-account-design.md §4）：
        - ``bindings`` 迁移必须晚于 ``_load_all()``（旧 ``single_service/multi_group``
          在 webui 配置里，要先读进内存）；
        - 迁移本身幂等（``kv["bindings_migrated"]``），二次启动只做加载。
        """
        # 账号身份迁移必须最先跑：把旧库的 bot_index 身份换成 account_id，
        # 之后所有读取（_load_all / 网关 / API）才有一致的 id 可用。
        await migrate_account_identity(self.db)
        await self._ensure_schema()
        await self._migrate_legacy_if_needed()
        await self._load_all()
        await self._load_bindings()
        await self._load_channel_policies()
        await self._load_known_groups()
        await self._load_channel_overrides()
        await self._migrate_bindings_if_needed()

    async def _ensure_schema(self) -> None:
        """老库补列：`conn_mode`（连接方向）晚于 `bots` 表引入。

        存量库的 `bots` 表只有 ws_url/owner_id/auto_connect；不补列会让
        `SELECT *` 之后所有读取都拿不到连接模式，反向账号被静默当成正向去连。
        默认值 `forward` 保证升级后存量账号行为不变。
        """
        await self.db.ensure_columns("bots", {"conn_mode": "TEXT NOT NULL DEFAULT 'forward'"})

    async def _migrate_legacy_if_needed(self) -> None:
        count = await self.db.fetchone("SELECT COUNT(*) AS c FROM module_config")
        if count and count["c"] > 0:
            return  # 已迁移
        await self._migrate_legacy()

    async def _migrate_legacy(self) -> None:
        """从旧 JSON 文件迁移数据（幂等：仅当 DB 为空时执行）。"""
        migrated = []

        # 1. bots
        bots_file = self.root / "webserver" / "webconfig.json"
        if bots_file.exists():
            try:
                data = json.loads(bots_file.read_text(encoding="utf-8"))
                bots = data.get("bots", [])
                for i, cfg in enumerate(bots):
                    account_id = f"bot{i + 1}"
                    await self.db.execute(
                        "INSERT OR REPLACE INTO bots "
                        "(account_id, name, ws_url, owner_id, auto_connect, sort_order) "
                        "VALUES (?,?,?,?,?,?)",
                        (account_id, f"账号 {i + 1}", cfg.get("ws_url", ""), cfg.get("owner_id"),
                         1 if cfg.get("auto_connect") else 0, i),
                    )
                migrated.append(f"bots({len(bots)})")
            except Exception as e:
                self.log.error(f"[Config] 迁移 bots 失败: {e}")

        # 2. webui 配置
        webui_file = self.root / "webui" / "webui_config.json"
        if webui_file.exists():
            try:
                data = json.loads(webui_file.read_text(encoding="utf-8"))
                for key, value in data.items():
                    await self.db.execute(
                        "INSERT OR REPLACE INTO webui_config (key, value_json) VALUES (?,?)",
                        (key, self.db.dumps(value)),
                    )
                migrated.append("webui")
            except Exception as e:
                self.log.error(f"[Config] 迁移 webui 配置失败: {e}")

        # 3. 模块配置 / 权限（<module_dir>/configs/<name>/{config.json,authority.json}）
        #    两个候选：`data/module/configs`（现在的位置）与 `module/configs`（老布局，
        #    启动迁移会把前者搬过来，但 DB 导入早于/晚于迁移都不该丢数据）。
        for configs_dir in (self.root / "data" / "module" / "configs", self.root / "module" / "configs"):
            if not configs_dir.exists():
                continue
            for cfg_dir in sorted(configs_dir.iterdir()):
                if not cfg_dir.is_dir() or cfg_dir.name.startswith(("_", ".")):
                    continue
                cfg_file = cfg_dir / "config.json"
                auth_file = cfg_dir / "authority.json"
                if cfg_file.exists():
                    await self._migrate_module_json(cfg_dir.name, "config", cfg_file)
                if auth_file.exists():
                    await self._migrate_module_json(cfg_dir.name, "authority", auth_file)

        if migrated:
            self.log.info(f"[Config] 已从旧 JSON 迁移: {', '.join(migrated)}")

    async def _migrate_module_json(self, module: str, kind: str, path: Path) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if not isinstance(data, dict):
            return
        items = data.items()
        for bot_id, value in items:
            if kind == "config":
                if isinstance(value, dict):
                    await self.db.execute(
                        "INSERT OR REPLACE INTO module_config (module_name, bot_id, config_json, updated_at) VALUES (?,?,?,?)",
                        (module, bot_id, self.db.dumps(value), int(asyncio.get_running_loop().time())),
                    )
            else:  # authority
                if isinstance(value, dict):
                    await self.db.execute(
                        "INSERT OR REPLACE INTO module_authority "
                        "(module_name, bot_id, enabled, group_mode, group_list, user_mode, user_list, updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (
                            module, bot_id,
                            1 if value.get("enabled", True) else 0,
                            value.get("group_mode", "blacklist"),
                            self.db.dumps(value.get("group_list", [])),
                            value.get("user_mode", "blacklist"),
                            self.db.dumps(value.get("user_list", [])),
                            int(asyncio.get_running_loop().time()),
                        ),
                    )

    async def _load_all(self) -> None:
        # bots（按 sort_order 还原界面顺序；id 是唯一凭据，顺序只是展示）
        rows = await self.db.fetchall("SELECT * FROM bots ORDER BY sort_order, account_id")
        self._bots = {}
        for r in rows:
            owner_id = r["owner_id"]
            if owner_id is not None:
                try:
                    owner_id = int(owner_id)
                except (TypeError, ValueError):
                    pass
            account_id = str(r["account_id"])
            self._bots[account_id] = {
                "id": account_id,
                "name": normalize_account_name(r["name"], account_id),
                "ws_url": r["ws_url"],
                "owner_id": owner_id,
                "auto_connect": bool(r["auto_connect"]),
                "conn_mode": normalize_mode(r.get("conn_mode")),
            }

        # 上次登录账号快照（老库无此表时由 SCHEMA 的 CREATE TABLE IF NOT EXISTS 补建）
        rows = await self.db.fetchall("SELECT * FROM bot_accounts")
        self._bot_accounts = {}
        for r in rows:
            self._bot_accounts[str(r["account_id"])] = {
                "user_id": r["user_id"],
                "nickname": r["nickname"] or "",
                "last_login_at": int(r["last_login_at"] or 0),
            }

        # webui
        rows = await self.db.fetchall("SELECT * FROM webui_config")
        for r in rows:
            self._webui[r["key"]] = self.db.loads(r["value_json"], {})

        # module config / authority：运行时已统一到 bindings（见 `_load_bindings`），
        # 旧表只作为迁移源保留，这里不再加载（避免出现两套真相）。

        # provider presets
        rows = await self.db.fetchall("SELECT * FROM provider_presets")
        for r in rows:
            self._provider_presets[r["id"]] = {
                "id": r["id"],
                "name": r["name"],
                "provider": r["provider"],
                "config": self.db.loads(r["config_json"], {}),
                "enabled": bool(r["enabled"]),
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }

        # provider models
        rows = await self.db.fetchall("SELECT * FROM provider_models")
        for r in rows:
            self._provider_models[r["id"]] = {
                "id": r["id"],
                "preset_id": r["preset_id"],
                "model": r["model"],
                "provider_type": r["provider_type"],
                "enabled": bool(r["enabled"]),
                "config": self.db.loads(r["config_json"], {}),
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }

        # provider settings
        rows = await self.db.fetchall("SELECT * FROM provider_settings")
        for r in rows:
            self._provider_settings[r["key"]] = self.db.loads(r["value_json"], None)

        # role presets
        rows = await self.db.fetchall("SELECT * FROM role_presets")
        for r in rows:
            self._role_presets[r["id"]] = {
                "id": r["id"],
                "name": r["name"],
                "description": r["description"],
                "system_prompt": r["system_prompt"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }

        self._publish_account_labels()
        self.log.debug(
            f"[Config] 加载完成: bots={len(self._bots)} "
            f"bindings={sum(len(rows) for rows in self._bindings.values())} "
            f"presets={len(self._provider_presets)} models={len(self._provider_models)} roles={len(self._role_presets)}"
        )

    # ==================== 变更通知 ====================
    def on_change(self, listener: Listener) -> None:
        self._listeners.append(listener)

    async def _notify(self, scope: str, payload: Any = None) -> None:
        for listener in list(self._listeners):
            try:
                await listener(scope, payload)
            except Exception as e:
                self.log.error(f"[Config] 变更通知失败: {e}")

    # ==================== webui ====================
    def get_webui_config(self) -> dict:
        """返回深拷贝，防止调用方原地污染内存缓存。"""
        import copy

        return copy.deepcopy(self._webui)

    async def save_webui_config(self, config: dict) -> None:
        merged = {**DEFAULT_WEBUI_CONFIG, **config}
        sqls = [("DELETE FROM webui_config", ())]
        for key, value in merged.items():
            sqls.append(("INSERT OR REPLACE INTO webui_config (key, value_json) VALUES (?,?)",
                         (key, self.db.dumps(value))))
        await self.db.run_in_transaction(sqls)   # 先落库，成功后再更新内存缓存
        self._webui = merged
        await self._notify("webui", merged)

    # ==================== Provider 预设（全局共享连接配置） ====================
    def list_provider_presets(self) -> list[dict]:
        """返回全部 Provider 预设（深拷贝，调用方可安全修改）。"""
        import copy

        return [copy.deepcopy(self._provider_presets[k]) for k in sorted(self._provider_presets)]

    def get_provider_preset(self, preset_id: str) -> dict | None:
        """按 ID 返回 Provider 预设（深拷贝），不存在返回 None。"""
        import copy

        preset = self._provider_presets.get(str(preset_id))
        return copy.deepcopy(preset) if preset else None

    def upsert_provider_preset(self, preset_id: str, preset: dict) -> None:
        """更新内存缓存并异步落盘（无事件循环时仅内存生效）。"""
        key = str(preset_id)
        self._provider_presets[key] = dict(preset)
        self._schedule_persist(self._persist_provider_preset, key)

    async def save_provider_preset(self, preset_id: str, preset: dict) -> None:
        """持久化 Provider 预设并广播变更。"""
        key = str(preset_id)
        self._provider_presets[key] = dict(preset)
        await self._persist_provider_preset(key)
        await self._notify("provider_presets", self.list_provider_presets())

    async def delete_provider_preset(self, preset_id: str) -> bool:
        """删除 Provider 预设并广播变更。"""
        key = str(preset_id)
        if key not in self._provider_presets:
            return False
        del self._provider_presets[key]
        try:
            await self.db.execute("DELETE FROM provider_presets WHERE id = ?", (key,))
        except Exception as e:
            self.log.error(f"[Config] 删除 Provider 预设失败 {key}: {e}")
            return False
        await self._notify("provider_presets", self.list_provider_presets())
        return True

    async def _persist_provider_preset(self, preset_id: str) -> None:
        try:
            preset = self._provider_presets.get(preset_id)
            if preset is None:
                await self.db.execute("DELETE FROM provider_presets WHERE id = ?", (preset_id,))
                return
            await self.db.execute(
                "INSERT OR REPLACE INTO provider_presets "
                "(id, name, provider, config_json, enabled, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    preset_id,
                    preset.get("name", ""),
                    preset.get("provider", "openai"),
                    self.db.dumps(preset.get("config", {})),
                    1 if preset.get("enabled", True) else 0,
                    int(preset.get("created_at", 0)),
                    int(preset.get("updated_at", 0)),
                ),
            )
        except Exception as e:
            self.log.error(f"[Config] 持久化 Provider 预设失败 {preset_id}: {e}")

    # ==================== Provider 模型（连接预设下的模型实例） ====================
    def list_provider_models(self, preset_id: str | None = None) -> list[dict]:
        """返回模型实例列表（深拷贝），可按预设过滤。"""
        import copy

        models = [copy.deepcopy(self._provider_models[k]) for k in sorted(self._provider_models)]
        if preset_id:
            models = [m for m in models if m.get("preset_id") == preset_id]
        return models

    def get_provider_model(self, model_id: str) -> dict | None:
        import copy

        model = self._provider_models.get(str(model_id))
        return copy.deepcopy(model) if model else None

    async def save_provider_model(self, model_id: str, model: dict) -> None:
        """持久化模型实例并广播变更。"""
        key = str(model_id)
        self._provider_models[key] = dict(model)
        await self._persist_provider_model(key)
        await self._notify("provider_models", self.list_provider_models())

    async def delete_provider_model(self, model_id: str) -> bool:
        key = str(model_id)
        if key not in self._provider_models:
            return False
        del self._provider_models[key]
        try:
            await self.db.execute("DELETE FROM provider_models WHERE id = ?", (key,))
        except Exception as e:
            self.log.error(f"[Config] 删除 Provider 模型失败 {key}: {e}")
            return False
        await self._notify("provider_models", self.list_provider_models())
        return True

    async def _persist_provider_model(self, model_id: str) -> None:
        try:
            model = self._provider_models.get(model_id)
            if model is None:
                await self.db.execute("DELETE FROM provider_models WHERE id = ?", (model_id,))
                return
            await self.db.execute(
                "INSERT OR REPLACE INTO provider_models "
                "(id, preset_id, model, provider_type, enabled, config_json, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    model_id,
                    model.get("preset_id", ""),
                    model.get("model", ""),
                    model.get("provider_type", "chat"),
                    1 if model.get("enabled", True) else 0,
                    self.db.dumps(model.get("config", {})),
                    int(model.get("created_at", 0)),
                    int(model.get("updated_at", 0)),
                ),
            )
        except Exception as e:
            self.log.error(f"[Config] 持久化 Provider 模型失败 {model_id}: {e}")

    # ==================== Provider 全局设置 ====================
    def get_provider_settings(self) -> dict:
        import copy

        return {**DEFAULT_PROVIDER_SETTINGS, **copy.deepcopy(self._provider_settings)}

    async def save_provider_settings(self, settings: dict) -> None:
        merged = {**DEFAULT_PROVIDER_SETTINGS, **(settings or {})}
        self._provider_settings = dict(merged)
        for key, value in merged.items():
            await self.db.execute(
                "INSERT OR REPLACE INTO provider_settings (key, value_json) VALUES (?,?)",
                (key, self.db.dumps(value)),
            )
        await self._notify("provider_settings", self.get_provider_settings())

    # ==================== 角色预设 ====================
    def list_role_presets(self) -> list[dict]:
        """返回全部角色预设（深拷贝，调用方可安全修改）。"""
        import copy

        return [copy.deepcopy(self._role_presets[k]) for k in sorted(self._role_presets)]

    def get_role_preset(self, role_id: str) -> dict | None:
        """按 ID 返回角色预设（深拷贝），不存在返回 None。"""
        import copy

        preset = self._role_presets.get(str(role_id))
        return copy.deepcopy(preset) if preset else None

    async def save_role_preset(self, role_id: str, preset: dict) -> None:
        key = str(role_id)
        self._role_presets[key] = dict(preset)
        await self._persist_role_preset(key)
        await self._notify("role_presets", self.list_role_presets())

    async def delete_role_preset(self, role_id: str) -> bool:
        key = str(role_id)
        if key not in self._role_presets:
            return False
        del self._role_presets[key]
        try:
            await self.db.execute("DELETE FROM role_presets WHERE id = ?", (key,))
        except Exception as e:
            self.log.error(f"[Config] 删除角色预设失败 {key}: {e}")
            return False
        await self._notify("role_presets", self.list_role_presets())
        return True

    async def _persist_role_preset(self, role_id: str) -> None:
        try:
            preset = self._role_presets.get(role_id)
            if preset is None:
                await self.db.execute("DELETE FROM role_presets WHERE id = ?", (role_id,))
                return
            await self.db.execute(
                "INSERT OR REPLACE INTO role_presets "
                "(id, name, description, system_prompt, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (
                    role_id,
                    preset.get("name", ""),
                    preset.get("description", ""),
                    preset.get("system_prompt", ""),
                    int(preset.get("created_at", 0)),
                    int(preset.get("updated_at", 0)),
                ),
            )
        except Exception as e:
            self.log.error(f"[Config] 持久化角色预设失败 {role_id}: {e}")

    def _schedule_persist(self, persist_fn: Callable[..., Any], *args: Any) -> None:
        try:
            asyncio.get_running_loop().create_task(persist_fn(*args))
        except RuntimeError:
            pass  # 无运行中的事件循环（测试环境）：仅内存生效
